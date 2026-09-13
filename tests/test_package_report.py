"""できあがったパッケージを、人が読める形にまとめられること。

画面に出すのはここで作った表なので、**中身が空になっていないか**を押さえる。
「ファイルは並ぶが、処理の記録が 1 件も出ない」といった壊れ方は、
画面を見ないと気づけない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archival_packager.core import aip_pipeline, bundled, package_report, sip_pipeline
from archival_packager.core.aip_models import AIPOptions
from archival_packager.core.models import SIPMetadata, SIPOptions


@pytest.fixture
def sip(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    (src / "文書").mkdir(parents=True)
    (src / "a.txt").write_text("資料 A\n", encoding="utf-8")
    (src / "文書" / "b.txt").write_text("資料 B\n", encoding="utf-8")

    out = tmp_path / "sip-out"
    out.mkdir()
    result = sip_pipeline.run(
        input_path=src,
        output_parent=out,
        metadata=SIPMetadata(identifier="2026-移管", title="総務課文書"),
        options=SIPOptions(),
        progress=lambda _m: None,
    )
    return result.sip_path


@pytest.fixture
def aip(sip: Path, tmp_path: Path) -> Path:
    out = tmp_path / "aip-out"
    out.mkdir()
    result = aip_pipeline.run(
        sip_root=sip,
        output_parent=out,
        options=AIPOptions(),
        progress=lambda _m: None,
    )
    return result.aip_path


class TestAIP:
    def test_reads_the_overview(self, aip: Path):
        report = package_report.read(aip)
        assert report.overview.kind == "AIP"
        assert report.overview.title == "総務課文書"
        assert report.overview.identifier == "2026-移管"
        assert report.overview.created, "作成日時が空"
        assert report.overview.file_count > 0
        assert report.overview.total_bytes > 0

    def test_lists_files_with_their_technical_facts(self, aip: Path):
        report = package_report.read(aip)
        originals = [f for f in report.files if f.use == "原本"]
        assert originals, "原本が 1 件も出ていない"
        for f in originals:
            assert f.path
            assert f.sha256, f"{f.path} のチェックサムが空"
            assert f.size > 0

    def test_shows_what_was_done(self, aip: Path):
        """**ここが本題。** PREMIS のイベントが読めなければ、
        「処理の実行がそのまま保存処理記録になる」と言えない。"""
        report = package_report.read(aip)
        assert report.events, "処理の記録が 1 件も出ていない"
        labels = {e.type_label for e in report.events}
        assert "取り込み" in labels
        assert "フォーマットの識別" in labels
        for e in report.events:
            assert e.date_time, f"{e.type_label} に日時が無い"
            assert e.outcome, f"{e.type_label} に結果が無い"

    def test_events_name_the_tool(self, aip: Path):
        """どのツールで行ったかが分からないと、記録として使えない。"""
        report = package_report.read(aip)
        identified = [e for e in report.events if e.type_label == "フォーマットの識別"]
        assert identified
        assert any(e.agent for e in identified), "実行したツールが空"

    def test_events_are_in_order(self, aip: Path):
        report = package_report.read(aip)
        stamps = [e.date_time for e in report.events]
        assert stamps == sorted(stamps)


class TestSIP:
    def test_reads_the_inventory(self, sip: Path):
        report = package_report.read(sip)
        assert report.overview.kind == "SIP"
        assert report.overview.file_count > 0
        assert report.overview.title == "総務課文書"
        assert all(f.sha256 for f in report.files)

    def test_keeps_the_virus_column(self, sip: Path):
        """検査したかどうかは、表から読めること。"""
        report = package_report.read(sip)
        assert all(f.virus for f in report.files)


class TestBrokenInput:
    def test_empty_directory_does_not_raise(self, tmp_path: Path):
        report = package_report.read(tmp_path)
        assert report.overview.note, "読めなかった理由を伝えていない"
        assert report.files == []

    def test_broken_mets_is_reported(self, tmp_path: Path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "METS.broken.xml").write_text("<mets", encoding="utf-8")
        report = package_report.read(tmp_path)
        assert "METS を読めませんでした" in report.overview.note


def test_human_bytes():
    assert package_report.human_bytes(512) == "512 B"
    assert package_report.human_bytes(2048) == "2.0 KB"


class TestSubmissionDocuments:
    """提出書類は PREMIS の object を持たない。それでも一覧に出し、
    サイズは実物から測ること（0 B と並ぶと壊れて見える）。"""

    def test_listed_with_their_size(self, aip: Path):
        report = package_report.read(aip)
        docs = [f for f in report.files if f.use == "提出書類"]
        assert docs, "提出書類が一覧に出ていない"
        assert all(f.size > 0 for f in docs), "サイズが 0 のままの行がある"

    def test_counted_apart_from_originals(self, aip: Path):
        report = package_report.read(aip)
        assert report.overview.original_count < report.overview.file_count
        assert report.overview.original_count > 0


#: 同梱の siegfried が無い環境（Linux の CI）では、識別そのものが走らない。
#: フォーマット名を確かめるテストは、そこでは意味を持たない。
needs_siegfried = pytest.mark.skipif(
    bundled.find("sf") is None, reason="同梱 sf が無い環境"
)


@needs_siegfried
class TestNormalizedCopiesAreNamed:
    """変換して作ったファイルにも、フォーマット名を付けること。

    PUID だけ入れて名前を空にしていたため、METS には規定値の "unknown" が
    書かれ、画面では「未識別」に見えていた。変換したものが未識別に見えるのは
    実態と違ううえ、担当者を無用に不安にさせる。
    """

    @pytest.fixture
    def aip_with_png(self, tmp_path: Path) -> Path:
        import base64

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        src = tmp_path / "in"
        src.mkdir()
        (src / "写真.png").write_bytes(png)

        out = tmp_path / "sip-out"
        out.mkdir()
        sip = sip_pipeline.run(
            input_path=src, output_parent=out,
            metadata=SIPMetadata(identifier="x", title="写真"),
            options=SIPOptions(), progress=lambda _m: None,
        ).sip_path

        aout = tmp_path / "aip-out"
        aout.mkdir()
        return aip_pipeline.run(
            sip_root=sip, output_parent=aout, options=AIPOptions(),
            progress=lambda _m: None,
        ).aip_path

    def test_the_original_png_is_identified(self, aip_with_png: Path):
        report = package_report.read(aip_with_png)
        png = next(f for f in report.files if f.path.endswith(".png"))
        assert png.puid == "fmt/11"
        assert png.format_name == "Portable Network Graphics"

    def test_the_preservation_copy_has_a_name(self, aip_with_png: Path):
        report = package_report.read(aip_with_png)
        tiff = next(f for f in report.files if f.use == "保存用")
        assert tiff.puid == "fmt/353"
        assert tiff.format_name and tiff.format_name != "unknown", (
            "変換後のファイルが未識別に見える"
        )

    def test_nothing_counts_as_unidentified(self, aip_with_png: Path):
        report = package_report.read(aip_with_png)
        assert report.summary.unidentified == 0
        assert report.summary.normalized == 1


class TestSummary:
    """まとまりで見た数。1 件ずつ並べても全体は掴めない。"""

    def test_counts_formats_and_events(self, aip: Path):
        report = package_report.read(aip)
        assert report.summary.formats, "フォーマットごとの件数が空"
        assert sum(n for _name, n in report.summary.formats) == report.overview.original_count
        assert report.summary.events, "処理の種類ごとの件数が空"

    def test_formats_are_ordered_by_count(self, aip: Path):
        report = package_report.read(aip)
        counts = [n for _name, n in report.summary.formats]
        assert counts == sorted(counts, reverse=True)

    def test_inherits_the_virus_column_into_the_aip(self, aip: Path):
        """ウイルス検査の結果は METS では表せないので、
        引き継いだ技術インベントリから拾えていること。"""
        report = package_report.read(aip)
        originals = [f for f in report.files if f.use == "原本"]
        assert all(f.virus for f in originals), "ウイルス検査の列が空"


class TestMaliciousMetsCannotReadLocalFiles:
    """細工された METS に手元のファイルを読み出させない（XXE）。

    **これは実在した穴である。** lxml 6.1.1 の既定パーサは
    `resolve_entities="internal"` で、外部**パラメータ**実体を経由した形なら
    任意のローカルファイルを読み出す（2026-09-12 に実測。lxml 6.1.3 で上流修正）。

    最初に書いたテストは**一般実体**の形しか試しておらず、対策を外しても落ちなかった。
    そのため「既定でも安全なので対策は不要だった」と誤って結論しかけた。
    **落ちないテストは、対策が要らない証拠ではなく、攻撃が弱い証拠である。**
    ここでは両方の形を試す。
    """

    def _package(self, tmp_path: Path, secret: Path, *, kind: str = "parameter") -> Path:
        """kind="general" は一般実体、"parameter" は外部パラメータ実体。

        後者が本命。前者は lxml 6.1.1 の既定でも「実体が未定義」で落ちるため、
        これだけだと対策の有無を見分けられない。

        **URL は `Path.as_uri()` で組み立てる。** `file://` に文字列連結で
        パスを足すと、Windows では円記号区切りのパスがそのまま並んだ不正な URI に
        なり、lxml が DOCTYPE の時点で構文エラーを投げる。守っている当人を試す前に
        落ちるので、攻撃が成立していないだけなのに「防いだ」ようにも見える
        （2026-09-13、Windows の CI で発覚）。
        """
        root = tmp_path / f"わるいパッケージ-{kind}"
        (root / "data").mkdir(parents=True)
        if kind == "general":
            doctype = f'''<!DOCTYPE mets [
  <!ENTITY leak SYSTEM "{secret.as_uri()}">
]>'''
        else:
            dtd = tmp_path / "ext.dtd"
            dtd.write_text(
                f'''<!ENTITY % file SYSTEM "{secret.as_uri()}">
<!ENTITY % wrap "<!ENTITY leak '%file;'>">
%wrap;
''', encoding="utf-8")
            doctype = f'''<!DOCTYPE mets [
  <!ENTITY % ext SYSTEM "{dtd.as_uri()}">
  %ext;
]>'''
        mets = f"""<?xml version="1.0" encoding="UTF-8"?>
{doctype}
<mets:mets xmlns:mets="http://www.loc.gov/METS/"
           xmlns:xlink="http://www.w3.org/1999/xlink">
  <mets:metsHdr CREATEDATE="2026-09-12T00:00:00Z">
    <mets:agent ROLE="CREATOR" TYPE="OTHER">
      <!-- 実体は「要素の中身」に置く。XML は属性値の中に外部実体を書くことを
           禁じているので、属性に置くと攻撃そのものが成立しない。 -->
      <mets:name>&leak;</mets:name>
    </mets:agent>
  </mets:metsHdr>
  <mets:fileSec>
    <mets:fileGrp USE="original">
      <mets:file ID="f1">
        <mets:FLocat LOCTYPE="URL" xlink:href="objects/a.txt"/>
      </mets:file>
    </mets:fileGrp>
  </mets:fileSec>
  <mets:structMap/>
</mets:mets>
"""
        (root / "data" / "METS.xml").write_text(mets, encoding="utf-8")
        return root

    @pytest.mark.parametrize("kind", ["general", "parameter"])
    def test_secret_does_not_leak(self, tmp_path: Path, kind: str):
        # 名前は ASCII にする。file:// URL に日本語が入ると解決されず、
        # 攻撃が「成功したが中身は空」になって、テストが無言で無力化する。
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-VALUE", encoding="utf-8")
        root = self._package(tmp_path, secret, kind=kind)

        try:
            report = package_report.read(root)
        except Exception as exc:
            # 読めずに失敗するのは構わない。漏れないことが要件。
            assert "TOP-SECRET-VALUE" not in str(exc)
            return

        assert "TOP-SECRET-VALUE" not in repr(report)

    def test_the_module_parser_blocks_it(self, tmp_path: Path):
        """**これが本体の検査。** モジュールが使うパーサそのものを当てる。

        `read()` の戻り値を見る形にしていたときは、実体の値が表に出ない経路だったため、
        対策を外しても緑のままだった。**守っている当人を直接試すこと。**

        要件は「秘密が漏れないこと」であって、解析が成功することではない。
        防ぎ方は環境によって 2 通りに分かれる（2026-09-13 に実測）。

          macOS  : 解析は通り、`&leak;` は値を持たない
          Windows: `Entity 'leak' not defined` で解析が中断する

        libxml2 の版や構成の違いで、未定義の実体を落とすかエラーにするかが
        変わる。**どちらも安全なので、どちらも通す。** 以前は解析の成功まで
        求めていたため、Windows で「防げているのに落ちる」状態になっていた。

        例外を許すと「何が起きても通るテスト」になりかねないが、すぐ下の
        `test_the_parameter_entity_form_is_the_one_that_matters` が、同じ
        ペイロードが素の lxml では実際に漏れることをその環境で確かめている。
        あちらが通っている限り、ここは攻撃が有効な状態で防御を試している。
        """
        from lxml import etree

        from archival_packager.core import package_report as pr

        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-VALUE", encoding="utf-8")
        root = self._package(tmp_path, secret, kind="parameter")
        mets = root / "data" / "METS.xml"

        try:
            doc = etree.parse(str(mets), pr._SAFE_PARSER)
        except etree.XMLSyntaxError as exc:
            assert "TOP-SECRET-VALUE" not in str(exc), "エラーの本文に漏れている"
            return
        assert "TOP-SECRET-VALUE" not in etree.tostring(doc, encoding="unicode")

    def test_the_parameter_entity_form_is_the_one_that_matters(self, tmp_path: Path):
        """対策を外したときに実際に漏れる形であることを、素の lxml で確かめる。

        このテスト自体が「攻撃ペイロードが有効であること」の検査。
        ここが通らなくなったら、上のテストは何も守っていない。
        """
        from lxml import etree

        # 名前は ASCII にする。file:// URL に日本語が入ると解決されず、
        # 攻撃が「成功したが中身は空」になって、テストが無言で無力化する。
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-VALUE", encoding="utf-8")
        root = self._package(tmp_path, secret, kind="parameter")
        mets = root / "data" / "METS.xml"

        leaked = ""
        try:
            doc = etree.parse(str(mets))          # 既定パーサ＝対策なし
            leaked = etree.tostring(doc, encoding="unicode")
        except Exception:
            pytest.skip("この lxml の既定パーサでは外部実体が解決されない（上流で修正済み）")
        assert "TOP-SECRET-VALUE" in leaked, "攻撃が効いていない。ペイロードを見直すこと"
