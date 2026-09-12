"""AIP パイプラインの結合テスト（SIP から AIP まで通す）。

SIP 側の出力をそのまま入力に使う。2 つのパイプラインが実際に繋がっていることが
確認したいことなので、SIP を手で組み立てず sip_pipeline に作らせる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import aip_pipeline, conversion_registry, sip_pipeline, sip_reader
from archival_packager.core.aip_models import (
    AIPOptions,
    AIPPipelineError,
    DescriptiveMetadata,
    FixityOutcome,
)
from archival_packager.core.mets import METS_NS, PREMIS_NS
from archival_packager.core.models import SIPMetadata, SIPOptions

NS = {"mets": METS_NS, "premis": PREMIS_NS}


@pytest.fixture(autouse=True)
def builtin_rules_only(tmp_path):
    """開発機に置いてある本物の rules.toml でテストの通り方が変わらないようにする。

    規則表は利用者の環境から読む。テストが環境に左右されると、落ちたときに
    アプリの不具合なのか手元の設定なのかを切り分けられない。
    """
    conversion_registry.reload(tmp_path / "no-such-rules.toml")
    yield
    conversion_registry.reload(tmp_path / "no-such-rules.toml")


@pytest.fixture
def sip(tmp_path: Path) -> Path:
    """SIP パイプラインで実際に SIP を作って返す。"""
    src = tmp_path / "in"
    (src / "文書").mkdir(parents=True)
    (src / "a.txt").write_text("資料 A\n", encoding="utf-8")
    (src / "文書" / "b.txt").write_text("資料 B\n", encoding="utf-8")

    out = tmp_path / "sip-out"
    out.mkdir()
    result = sip_pipeline.run(
        input_path=src,
        output_parent=out,
        metadata=SIPMetadata(identifier="2026-移管", title="総務課文書", scope_note="範囲の説明"),
        options=SIPOptions(),
        progress=lambda _m: None,
    )
    return result.sip_path


@pytest.fixture
def sip_with_image(tmp_path: Path) -> Path:
    """PNG を 1 枚だけ入れた SIP。正規化の経路を通しで確かめるために使う。"""
    from PIL import Image

    src = tmp_path / "img-in"
    src.mkdir()
    Image.new("RGB", (16, 16), (200, 100, 50)).save(src / "写真.png")

    out = tmp_path / "img-sip-out"
    out.mkdir()
    sip_path = sip_pipeline.run(
        input_path=src,
        output_parent=out,
        metadata=SIPMetadata(identifier="2026-写真", title="写真資料"),
        options=SIPOptions(),
        progress=lambda _m: None,
    ).sip_path

    # PUID は SIP の formats.csv から継承される。ここで直接書いておくことで、
    # このテストが siegfried の同梱有無に左右されないようにする
    # （バイナリは配布物に含めないので、CI では sf が無い）。
    formats = sip_path / "metadata" / "submissionDocumentation" / "formats.csv"
    formats.write_text(
        "﻿相対パス,フォーマット名,PRONOM,MIME,拡張子警告,サイズ(バイト),更新日時,SHA-256\r\n"
        "写真.png,Portable Network Graphics,fmt/11,image/png,,0,,\r\n",
        encoding="utf-8",
    )
    return sip_path


def run_aip(sip: Path, tmp_path: Path, **opts) -> tuple:
    out = tmp_path / "aip-out"
    out.mkdir(exist_ok=True)
    messages: list[str] = []
    result = aip_pipeline.run(
        sip_root=sip,
        output_parent=out,
        options=AIPOptions(**opts),
        progress=messages.append,
    )
    return result, messages


class TestEndToEnd:
    def test_produces_a_valid_aip_bag(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        aip_pipeline.validate_aip(result.aip_path)  # 失敗すれば例外

        assert (result.aip_path / "data" / "objects" / "a.txt").is_file()
        assert (result.aip_path / "data" / "objects" / "文書" / "b.txt").is_file()
        assert (result.aip_path / "data" / "logs" / "README.txt").is_file()
        assert result.mets_path.is_file()
        assert result.original_count == 2

    def test_mets_is_named_after_the_aip_uuid(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        assert result.mets_path.name == f"METS.{result.aip_uuid}.xml"

    def test_submission_documentation_is_inherited(self, sip, tmp_path):
        """SIP 段で作った記録を AIP に持ち込むこと。"""
        result, _ = run_aip(sip, tmp_path)
        inherited = result.aip_path / "data" / "objects" / "submissionDocumentation"
        assert (inherited / "formats.csv").is_file()
        assert (inherited / "accession.csv").is_file()

    def test_progress_reaches_the_caller(self, sip, tmp_path):
        _result, messages = run_aip(sip, tmp_path)
        joined = "\n".join(messages)
        assert "完全性を確認しています" in joined
        assert "完了しました。" in joined


class TestInheritance:
    def test_hashes_and_puids_come_from_the_sip(self, sip, tmp_path):
        """SIP 段の結果を作り直さないこと。

        作り直すと「受入時の記録」と「保存時の記録」が食い違う余地が生まれる。
        """
        parsed = sip_reader.read(sip)
        assert parsed.inherited_hashes == 2
        assert parsed.recomputed_hashes == 0
        assert all(f.sha256 for f in parsed.files)

    def test_recomputes_when_the_sip_has_no_manifest(self, tmp_path):
        bare = tmp_path / "bare-sip"
        (bare / "objects").mkdir(parents=True)
        (bare / "objects" / "a.txt").write_text("x", encoding="utf-8")

        parsed = sip_reader.read(bare)
        assert parsed.recomputed_hashes == 1
        assert parsed.files[0].sha256 is not None

    def test_descriptive_metadata_flows_into_dmdsec(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        root = etree.fromstring(result.mets_path.read_bytes())
        titles = root.xpath("//mets:dmdSec//*[local-name()='title']/text()", namespaces=NS)
        assert "総務課文書" in titles, "SIP の description.csv から継承する"

    def test_user_metadata_overrides_inherited(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path, descriptive=DescriptiveMetadata(title="上書きタイトル"))
        root = etree.fromstring(result.mets_path.read_bytes())
        titles = root.xpath("//mets:dmdSec//*[local-name()='title']/text()", namespaces=NS)
        assert "上書きタイトル" in titles
        assert "総務課文書" not in titles

    def test_submission_docs_are_not_treated_as_originals(self, sip, tmp_path):
        """継承した提出書類を原本として二重に数えないこと。"""
        result, _ = run_aip(sip, tmp_path)
        parsed = sip_reader.read(sip)
        assert all("submissionDocumentation" not in f.relative_path for f in parsed.files)
        assert result.original_count == 2


class TestFixity:
    def test_passes_for_an_untouched_sip(self, sip, tmp_path):
        result, messages = run_aip(sip, tmp_path)
        assert result.fixity.outcome is FixityOutcome.PASSED
        assert "すべて一致" in "\n".join(messages)

    def test_tampering_is_recorded_but_does_not_stop_the_aip(self, sip, tmp_path):
        """不一致でも AIP 化は続ける。ただし警告と PREMIS に必ず残す。

        止めてしまうと「壊れた SIP は保存すらできない」ことになり、
        現物を記録として残す機会を失う。
        """
        (sip / "objects" / "a.txt").write_text("改竄", encoding="utf-8")
        result, _ = run_aip(sip, tmp_path)

        assert result.fixity.outcome is FixityOutcome.FAILED
        assert any("不一致" in w for w in result.warnings)

        root = etree.fromstring(result.mets_path.read_bytes())
        outcomes = root.xpath(
            "//premis:event[premis:eventType='fixity check']/premis:eventOutcomeInformation"
            "/premis:eventOutcome/text()",
            namespaces=NS,
        )
        assert "fail" in outcomes

    def test_skip_is_recorded_distinctly(self, tmp_path):
        """「照合できなかった」を「一致した」と読み違えられないこと。"""
        bare = tmp_path / "bare-sip"
        (bare / "objects").mkdir(parents=True)
        (bare / "objects" / "a.txt").write_text("x", encoding="utf-8")

        result, _ = run_aip(bare, tmp_path)
        assert result.fixity.outcome is FixityOutcome.SKIPPED
        assert any("スキップ" in w for w in result.warnings)


class TestPremisRecords:
    def test_ingestion_and_fixity_events_recorded(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        root = etree.fromstring(result.mets_path.read_bytes())
        types = set(root.xpath("//premis:eventType/text()", namespaces=NS))
        assert {"ingestion", "fixity check"} <= types

    def test_archivist_recorded_as_human_agent(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path, archivist_name="中村 覚")
        root = etree.fromstring(result.mets_path.read_bytes())
        names = root.xpath("//premis:agentName/text()", namespaces=NS)
        types = root.xpath("//premis:agentType/text()", namespaces=NS)
        assert "中村 覚" in names
        assert "human" in types

    def test_no_human_agent_when_name_absent(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        root = etree.fromstring(result.mets_path.read_bytes())
        assert "human" not in root.xpath("//premis:agentType/text()", namespaces=NS)

    def test_event_identifiers_are_unique(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        root = etree.fromstring(result.mets_path.read_bytes())
        ids = root.xpath("//premis:eventIdentifierValue/text()", namespaces=NS)
        assert len(ids) == len(set(ids)), "event ごとに別の識別子"


class TestNormalization:
    def test_disabled_by_option(self, sip, tmp_path):
        result, messages = run_aip(sip, tmp_path, normalize=False)
        assert result.derivative_count == 0
        assert "変換は行いません" in "\n".join(messages)

    def test_nothing_to_do_for_text_files(self, sip, tmp_path):
        """既に保存に適した形式・未知の形式は原本のまま。"""
        result, messages = run_aip(sip, tmp_path, normalize=True)
        assert result.derivative_count == 0
        assert "対象はありません" in "\n".join(messages)

    def test_failure_is_a_warning_not_a_stop(self, sip, tmp_path, monkeypatch):
        """1 ファイルの変換失敗で移管全体を止めない。原本はそのまま保存される。"""
        from archival_packager.core import conversion_registry
        from archival_packager.core.aip_models import DerivativePurpose, NormalizationRule

        rule = NormalizationRule(
            puid_in="fmt/any", purpose=DerivativePurpose.PRESERVATION,
            tool="nonexistent-tool", args=["{in}", "{out}"], out_extension="tiff",
        )
        monkeypatch.setattr(conversion_registry, "rule_for", lambda puid, purpose: rule)
        monkeypatch.setattr(
            aip_pipeline.conversion_registry, "rule_for", lambda puid, purpose: rule
        )

        result, _ = run_aip(sip, tmp_path, normalize=True)
        assert result.derivative_count == 0
        assert any("原本のまま保存" in w for w in result.warnings)
        assert (result.aip_path / "data" / "objects" / "a.txt").is_file(), "原本は保存される"

        root = etree.fromstring(result.mets_path.read_bytes())
        outcomes = root.xpath(
            "//premis:event[premis:eventType='normalization']/premis:eventOutcomeInformation"
            "/premis:eventOutcome/text()",
            namespaces=NS,
        )
        assert "fail" in outcomes, "失敗も記録に残す"

    def test_missing_tool_is_distinguishable_from_a_broken_file(
        self, sip, tmp_path, monkeypatch
    ):
        """原因も対処も違うものを、同じ文言にまとめない。

        Ghostscript は AGPL のため同梱していない。gs が無い環境では
        PostScript/EPS が変換されないが、それは資料が壊れているのではなく
        環境にツールが無いだけ。report を読む人が区別できる必要がある。
        """
        from archival_packager.core import conversion_registry
        from archival_packager.core.aip_models import DerivativePurpose, NormalizationRule

        rule = NormalizationRule(
            puid_in="fmt/124", purpose=DerivativePurpose.PRESERVATION,
            tool="gs", args=["{in}", "{out}"], out_extension="pdf",
        )
        monkeypatch.setattr(conversion_registry, "rule_for", lambda puid, purpose: rule)
        monkeypatch.setattr(
            aip_pipeline.conversion_registry, "rule_for", lambda puid, purpose: rule
        )
        monkeypatch.setattr(aip_pipeline.normalizer, "locate", lambda tool: None)

        result, _ = run_aip(sip, tmp_path, normalize=True)
        joined = "\n".join(result.warnings)
        assert "変換ツールが無いため原本のまま保存" in joined
        assert "Ghostscript は同梱していません" in joined, "次に何をすればよいか分かること"
        assert "変換に失敗" not in joined, "ファイルが壊れているかのように読ませない"


class TestImageNormalizationEndToEnd:
    """画像 → TIFF を通しで確かめる。

    これまでこの経路は端から端まで動かしたことが無かった。変換に外部ツール
    （sips / ImageMagick）が要り、開発機にもテスト環境にも無かったため。
    アプリ内変換にしたことで初めて実際に検証できる。
    """

    def test_png_becomes_an_uncompressed_tiff_derivative(self, sip_with_image, tmp_path):
        from PIL import Image

        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        assert result.derivative_count == 1, "PNG が正規化されていない"

        tiff = result.aip_path / "data" / "objects" / "写真-preservation.tiff"
        assert tiff.is_file()
        with Image.open(tiff) as got:
            assert got.info.get("compression") == "raw"
            assert got.size == (16, 16)

        original = result.aip_path / "data" / "objects" / "写真.png"
        assert original.is_file(), "原本も残す"

    def test_premis_records_the_conversion(self, sip_with_image, tmp_path):
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        root = etree.fromstring(result.mets_path.read_bytes())

        notes = root.xpath(
            "//premis:event[premis:eventType='normalization']"
            "//premis:eventOutcomeDetailNote/text()",
            namespaces=NS,
        )
        assert any("Pillow" in n for n in notes), "何で変換したかが残っていない"

    def test_premis_records_which_rule_ran(self, sip_with_image, tmp_path):
        """どの規則が動いたかを残すこと。

        Archivematica は eventDetail に
        `ArchivematicaFPRCommandID="..."; program="convert"; version="ImageMagick ..."`
        と書く。同じ 3 点（規則・道具・版）が無いと、この派生物を別の環境で
        作り直せるかどうかを後から判断できない。道具と版だけでは、
        **同じ道具で別の設定を使った場合と区別が付かない。**
        """
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        root = etree.fromstring(result.mets_path.read_bytes())

        notes = root.xpath(
            "//premis:event[premis:eventType='normalization']"
            "//premis:eventOutcomeDetailNote/text()",
            namespaces=NS,
        )
        assert any('rule="image-to-tiff"' in n for n in notes), "どの規則か分からない"
        assert any('program="pillow"' in n for n in notes)
        assert any('version="Pillow' in n for n in notes)

    def test_the_original_is_byte_identical_in_the_aip(self, sip_with_image, tmp_path):
        """**このアプリの最重要の性質。** 変換しても原本は 1 バイトも変わらない。

        正規化は原本を読むだけで、書き込みは派生物にしか行わない。ここが
        崩れると、保存しようとしている当のものを壊すことになる。
        """
        from archival_packager.core.checksums import sha256_of

        before = sha256_of(sip_with_image / "objects" / "写真.png")
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)

        assert sha256_of(sip_with_image / "objects" / "写真.png") == before, "入力を書き換えた"
        assert sha256_of(result.aip_path / "data" / "objects" / "写真.png") == before, (
            "AIP に入った原本が原本でなくなっている"
        )

    def test_derivative_is_in_the_bag_manifest(self, sip_with_image, tmp_path):
        """派生物がマニフェストに載っていなければ、後の完全性確認から漏れる。"""
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        manifest = (result.aip_path / "manifest-sha256.txt").read_text(encoding="utf-8")
        assert "写真-preservation.tiff" in manifest


class TestRuleTableTravelsWithTheAip:
    """使った規則表そのものを AIP に入れる。

    Archivematica は PREMIS に FPR（規則の登録簿）の識別子だけを書き、規則の
    中身は中央の登録簿にある。**後年その登録簿が引けなくなると、識別子だけ
    残っても意味を失う。** 表を一緒に入れておけば、このパッケージ単体で
    「何をどう変換したか」の説明が付く。
    """

    def _rules_doc(self, result) -> Path:
        return (
            result.aip_path / "data" / "objects" / "submissionDocumentation"
            / "normalization-rules.toml"
        )

    def test_the_table_is_written_into_the_package(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        text = self._rules_doc(result).read_text(encoding="utf-8")
        assert "image-to-tiff" in text
        assert "postscript-to-pdf" in text

    def test_the_table_is_listed_in_the_mets(self, sip, tmp_path):
        """**AIP に入れているのに fileSec に無いと、METS だけを読む側からは
        存在しないことになる。**"""
        result, _ = run_aip(sip, tmp_path)
        root = etree.fromstring(result.mets_path.read_bytes())
        hrefs = root.xpath("//mets:FLocat/@xlink:href",
                           namespaces={**NS, "xlink": "http://www.w3.org/1999/xlink"})
        assert "objects/submissionDocumentation/normalization-rules.toml" in hrefs

    def test_the_table_is_covered_by_the_manifest(self, sip, tmp_path):
        """マニフェストに無ければ、後の完全性確認から漏れる。"""
        result, _ = run_aip(sip, tmp_path)
        manifest = (result.aip_path / "manifest-sha256.txt").read_text(encoding="utf-8")
        assert "normalization-rules.toml" in manifest

    def test_the_user_rules_are_in_there_too(self, sip, tmp_path):
        (tmp_path / "rules.toml").write_text(
            """
[[rule]]
id = "wav-to-flac"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["--best", "-o", "{out}", "{in}"]
out_extension = "flac"
""",
            encoding="utf-8",
        )
        conversion_registry.reload(tmp_path / "rules.toml")

        result, _ = run_aip(sip, tmp_path)
        text = self._rules_doc(result).read_text(encoding="utf-8")
        assert "wav-to-flac" in text
        assert "--best" in text, "引数まで残す（何をしたかは引数で決まる）"

    def test_a_broken_table_is_reported_but_does_not_stop_the_aip(self, sip, tmp_path):
        """表の書き間違いで資料を受け入れられなくなるのは本末転倒。

        ただし黙って捨てない。なぜ規則が効かないかは利用者にしか直せない。
        """
        (tmp_path / "rules.toml").write_text(
            """
[[rule]]
id = "odd"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{in}", "{out}", "{tmp}"]
out_extension = "flac"
""",
            encoding="utf-8",
        )
        conversion_registry.reload(tmp_path / "rules.toml")

        result, _ = run_aip(sip, tmp_path)
        assert any("変換規則表" in w and "{tmp}" in w for w in result.warnings)
        assert (result.aip_path / "data" / "objects" / "a.txt").is_file()


class TestUserRulesEndToEnd:
    """利用者が足した規則が、実際に AIP の中身まで届くこと。

    組み込みと同じ道を通っているかは、ここで初めて端から端まで確かめられる。
    """

    @pytest.fixture
    def fake_conv(self, tmp_path, fake_tool, monkeypatch):
        """PNG を受け取って何か書き出す外部コマンドを装う。

        本物の変換ツールが開発機や CI にあるかどうかで、テストの通り方が
        変わってはいけない。
        """
        fake = fake_tool(name="conv", output_text="converted")
        (tmp_path / "rules.toml").write_text(
            """
[[rule]]
id = "png-to-jp2"
puid_in = ["fmt/11"]
executor = "command"
tool = "conv"
args = ["{in}", "{out}"]
puid_out = "x-fmt/392"
format_name_out = "JPEG 2000"
out_extension = "jp2"
""",
            encoding="utf-8",
        )
        conversion_registry.reload(tmp_path / "rules.toml")
        monkeypatch.setattr(aip_pipeline.normalizer, "locate", lambda tool: fake)
        return fake

    def test_user_rule_replaces_the_builtin_one(self, sip_with_image, tmp_path, fake_conv):
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        assert result.derivative_count == 1
        assert (result.aip_path / "data" / "objects" / "写真-preservation.jp2").is_file()
        assert not (result.aip_path / "data" / "objects" / "写真-preservation.tiff").exists(), (
            "利用者の規則が組み込みより優先される"
        )
        assert (result.aip_path / "data" / "objects" / "写真.png").is_file(), "原本は残す"

    def test_premis_says_the_rule_came_from_the_user(
        self, sip_with_image, tmp_path, fake_conv
    ):
        """**組み込みか利用者のものかが、記録から分かること。**

        同じ資料から別の組織が別の AIP を作ったとき、違いの原因が
        「表を足したから」なのかを、後から見た人が判断できる必要がある。
        """
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        root = etree.fromstring(result.mets_path.read_bytes())
        notes = root.xpath(
            "//premis:event[premis:eventType='normalization']"
            "//premis:eventOutcomeDetailNote/text()",
            namespaces=NS,
        )
        assert any('rule="png-to-jp2"' in n for n in notes)
        assert any('ruleSource="user"' in n for n in notes)

    def test_builtin_rules_are_marked_as_builtin(self, sip_with_image, tmp_path):
        """利用者の表が無いときは、組み込みで動いたことが記録に残る。"""
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        root = etree.fromstring(result.mets_path.read_bytes())
        notes = root.xpath(
            "//premis:event[premis:eventType='normalization']"
            "//premis:eventOutcomeDetailNote/text()",
            namespaces=NS,
        )
        assert any('ruleSource="builtin"' in n for n in notes)


class TestGuards:
    def test_rejects_non_sip_input(self, tmp_path):
        not_a_sip = tmp_path / "random"
        not_a_sip.mkdir()
        (not_a_sip / "a.txt").write_text("x", encoding="utf-8")
        with pytest.raises(AIPPipelineError) as exc:
            run_aip(not_a_sip, tmp_path)
        assert "SIP として認識できません" in exc.value.message

    def test_detects_bag_input(self, tmp_path):
        src = tmp_path / "in"
        src.mkdir()
        (src / "a.txt").write_text("x", encoding="utf-8")
        out = tmp_path / "sip-out"
        out.mkdir()
        bagged = sip_pipeline.run(
            input_path=src, output_parent=out,
            metadata=SIPMetadata(identifier="bagged", title="t"),
            options=SIPOptions(make_bag=True), progress=lambda _m: None,
        ).sip_path

        assert sip_reader.detect_bag(bagged)
        result, _ = run_aip(bagged, tmp_path)
        assert result.original_count == 1

    def test_output_dir_never_overwrites(self, sip, tmp_path):
        first, _ = run_aip(sip, tmp_path)
        second, _ = run_aip(sip, tmp_path)
        assert first.aip_path != second.aip_path


class TestDerivativeValidationEndToEnd:
    """作った派生物を開き直した結果が、AIP の記録に残ること。

    **「検証していない」と「検証して通った」は別の事実である。** どちらも
    METS からは同じに見えていた（イベントが無いだけ）。保存用の派生物が
    実は読めないものだった、という壊れ方は開き直すまで分からない。

    ここで確かめているのは読み戻せるかどうかだけで、**形式の適合性検査
    （veraPDF / JHOVE 相当）ではない。** それらは Java 製で同梱できない。
    """

    def _notes(self, mets_path, event_type):
        root = etree.fromstring(mets_path.read_bytes())
        return root.xpath(
            f"//premis:event[premis:eventType='{event_type}']"
            "//premis:eventOutcomeDetailNote/text()",
            namespaces=NS,
        )

    def _outcomes(self, mets_path, event_type):
        root = etree.fromstring(mets_path.read_bytes())
        return root.xpath(
            f"//premis:event[premis:eventType='{event_type}']//premis:eventOutcome/text()",
            namespaces=NS,
        )

    def test_event_type_is_the_premis_vocabulary_term(self, sip_with_image, tmp_path):
        """**eventType は PREMIS の語彙どおり "validation"。**

        独自の名前を付けると、他のシステムがこの AIP を読んだときに何の
        記録なのか分からなくなる。綴りは固定する。
        """
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        assert self._outcomes(result.mets_path, "validation") == ["pass"]

    def test_the_reading_tool_and_version_are_recorded(self, sip_with_image, tmp_path):
        """何で開き直したかを残すこと。

        「読めた」はその道具のその版の挙動でしかない。道具が分からないと、
        後から同じ確認を再現できず、主張の範囲も読み取れない。
        """
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        assert any("Pillow" in n for n in self._notes(result.mets_path, "validation"))

        root = etree.fromstring(result.mets_path.read_bytes())
        agents = root.xpath(
            "//premis:event[premis:eventType='validation']"
            "//premis:linkingAgentIdentifierValue/text()",
            namespaces=NS,
        )
        assert any("Pillow" in a for a in agents)

    def test_unreadable_derivative_is_not_in_the_package(
        self, sip_with_image, tmp_path, monkeypatch
    ):
        """**開き直せなかった派生物は成果物に入れない。**

        中途半端な派生物を保存用として記録するほうが、変換できなかったと
        記録するより悪い。後から見た人は「保存用がある」と信じてしまい、
        原本ではなくそちらを使おうとする。

        変換が終了コード 0 で返りながら読めないものを書く事故を模す。
        """
        def broken(src, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"not a TIFF")
            return "壊れた出力"

        monkeypatch.setattr(aip_pipeline.normalizer.image_normalize, "to_tiff", broken)
        result, messages = run_aip(sip_with_image, tmp_path, normalize=True)

        assert result.derivative_count == 0
        assert not (result.aip_path / "data" / "objects" / "写真-preservation.tiff").exists()
        assert (result.aip_path / "data" / "objects" / "写真.png").is_file(), "原本は残す"
        # 黙って落とさない。利用者に届くこと（report と画面の両方）。
        assert any("読み戻せない" in w for w in result.warnings)
        assert any("読み戻せない" in m for m in messages)

    def test_a_discarded_derivative_is_recorded_as_a_failed_normalization(
        self, sip_with_image, tmp_path, monkeypatch
    ):
        """捨てたことが記録から分かること。

        normalization が success のまま派生物だけ無いと、METS を読んだ人は
        「あるはずのものが見当たらない」状態に置かれる。validation の失敗と
        合わせて、何が起きたのかが 1 か所で読み取れるようにする。
        """
        def broken(src, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"not a TIFF")
            return "壊れた出力"

        monkeypatch.setattr(aip_pipeline.normalizer.image_normalize, "to_tiff", broken)
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)

        assert self._outcomes(result.mets_path, "validation") == ["fail"]
        assert self._outcomes(result.mets_path, "normalization") == ["fail"]
        notes = self._notes(result.mets_path, "normalization")
        assert any("破棄" in n for n in notes)
        # どの規則が動こうとしたのかは残す（規則が無かったのと区別する）。
        assert any('rule="image-to-tiff"' in n for n in notes)

    def test_unsupported_output_format_is_skipped_not_passed(
        self, sip_with_image, tmp_path, fake_tool, monkeypatch
    ):
        """読み戻す道具が無い形式を「確認して通った」にしない。

        利用者の規則は任意の形式を出せる。黙って pass にすると、記録上は
        検査済みに見えてしまう。派生物そのものは残す（確認していないだけで、
        壊れていると判断したわけではない）。
        """
        fake = fake_tool(name="conv", output_text="converted")
        (tmp_path / "rules.toml").write_text(
            """
[[rule]]
id = "png-to-jp2"
puid_in = ["fmt/11"]
executor = "command"
tool = "conv"
args = ["{in}", "{out}"]
out_extension = "jp2"
""",
            encoding="utf-8",
        )
        conversion_registry.reload(tmp_path / "rules.toml")
        monkeypatch.setattr(aip_pipeline.normalizer, "locate", lambda tool: fake)

        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        assert result.derivative_count == 1, "確認できないことは破棄の理由にしない"
        assert self._outcomes(result.mets_path, "validation") == ["skipped"]
        assert any("未確認" in n for n in self._notes(result.mets_path, "validation"))

    def test_no_validation_event_when_nothing_was_converted(self, sip, tmp_path):
        """変換していないファイルに validation の event を付けないこと。

        確認する対象が無いのに記録だけがあると、何を確認したのか分からない。
        """
        result, _ = run_aip(sip, tmp_path, normalize=True)
        assert self._outcomes(result.mets_path, "validation") == []


class TestReadmeHtml:
    """人向けの案内を AIP の一番上に置く（Archivematica の AIP に合わせる）。

    ここまでの説明はすべて METS / PREMIS の中にあり、XML を読める人にしか
    届かない。**10 年後にこの bag を渡された人が、まず何を見ればよいか**を
    平文で残す。
    """

    def test_written_at_the_data_root(self, sip, tmp_path):
        result, _ = run_aip(sip, tmp_path)
        assert (result.aip_path / "data" / "README.html").is_file()

    def test_covered_by_the_payload_manifest(self, sip, tmp_path):
        """**payload に入れた以上、マニフェストに載っていること。**

        BagIt では data/ 配下に manifest に無いファイルがあると bag として
        不正になる。`bagit.make_bag` の前に書いているかどうかで決まるので、
        書く場所を動かすと静かに壊れる。
        """
        result, _ = run_aip(sip, tmp_path)
        manifest = (result.aip_path / "manifest-sha256.txt").read_text(encoding="utf-8")
        assert "data/README.html" in manifest
        aip_pipeline.validate_aip(result.aip_path)  # bag として妥当なままであること

    def test_points_at_the_actual_mets_file(self, sip, tmp_path):
        """METS のファイル名は AIP ごとに違う。案内が合っていること。"""
        result, _ = run_aip(sip, tmp_path)
        text = (result.aip_path / "data" / "README.html").read_text(encoding="utf-8")
        assert f"METS.{result.aip_uuid}.xml" in text

    def test_says_what_was_not_checked(self, sip, tmp_path):
        """**確認していないことを、確認したことと同じ場所に書く。**

        読み戻し確認を形式適合性検査と読み違えられると、このパッケージは
        実際より強い主張をしていることになる。
        """
        result, _ = run_aip(sip, tmp_path)
        text = (result.aip_path / "data" / "README.html").read_text(encoding="utf-8")
        assert "veraPDF" in text and "JHOVE" in text
