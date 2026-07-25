"""AIP パイプラインの結合テスト（SIP から AIP まで通す）。

SIP 側の出力をそのまま入力に使う。2 つのパイプラインが実際に繋がっていることが
確認したいことなので、SIP を手で組み立てず sip_pipeline に作らせる。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import aip_pipeline, sip_pipeline, sip_reader
from archival_packager.core.aip_models import AIPOptions, AIPPipelineError, DescriptiveMetadata, FixityOutcome
from archival_packager.core.mets import METS_NS, PREMIS_NS
from archival_packager.core.models import SIPMetadata, SIPOptions

NS = {"mets": METS_NS, "premis": PREMIS_NS}


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
        from archival_packager.core import conversion_registry, normalizer
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

    @pytest.fixture
    def sip_with_image(self, tmp_path: Path) -> Path:
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

    def test_derivative_is_in_the_bag_manifest(self, sip_with_image, tmp_path):
        """派生物がマニフェストに載っていなければ、後の完全性確認から漏れる。"""
        result, _ = run_aip(sip_with_image, tmp_path, normalize=True)
        manifest = (result.aip_path / "manifest-sha256.txt").read_text(encoding="utf-8")
        assert "写真-preservation.tiff" in manifest


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
