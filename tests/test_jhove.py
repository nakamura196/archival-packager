"""JHOVE による形式の適合性検査（core/jhove.py）。

出力の読み取りは固定の XML で、実際の判定は同梱の JHOVE で確かめる。
同梱が無い環境（CI のテスト用ジョブなど）では、実物を使うテストは飛ばす。
"""

from __future__ import annotations

import pytest
from PIL import Image

from archival_packager.core import jhove

needs_jhove = pytest.mark.skipif(jhove.locate() is None, reason="JHOVE が同梱されていない")

_OUTPUT = b"""<?xml version="1.0" encoding="UTF-8"?>
<jhove xmlns="http://schema.openpreservation.org/ois/xml/ns/jhove"
 name="Jhove" release="1.34.0" date="2025-07-02">
 <date>2026-10-27T19:16:48-07:00</date>
 <repInfo uri="0.tif">
  <reportingModule release="1.9.5" date="2024-08-22">TIFF-hul</reportingModule>
  <format>TIFF</format>
  <version>6.0</version>
  <status>Well-Formed and valid</status>
 </repInfo>
 <repInfo uri="1.tif">
  <reportingModule release="1.9.5" date="2024-08-22">TIFF-hul</reportingModule>
  <format>TIFF</format>
  <version>4.0</version>
  <status>Well-Formed, but not valid</status>
  <messages>
   <message severity="error" id="TIFF-HUL-29">Invalid strip offset</message>
  </messages>
 </repInfo>
 <repInfo uri="2.tif">
  <status>Unknown</status>
 </repInfo>
</jhove>
"""


class TestParseOutput:
    def test_reads_status_module_and_messages(self):
        """判定・部品の版・メッセージを取り出せること。

        メッセージは利用者に見せる不適合の理由になる。ID まで残すのは、
        JHOVE の wiki で意味を引けるようにするため。
        """
        results, release = jhove.parse_output(_OUTPUT)

        assert release == "1.34.0"
        assert results["0.tif"].conforms is True
        assert results["0.tif"].module == "TIFF-hul 1.9.5"
        assert results["1.tif"].conforms is False
        assert results["1.tif"].messages == ["error: Invalid strip offset (TIFF-HUL-29)"]

    def test_unknown_is_neither_conforming_nor_not(self):
        """**"Unknown" を不適合と読まない。** 読めなかっただけのことがある。"""
        results, _ = jhove.parse_output(_OUTPUT)
        assert results["2.tif"].conforms is None

    def test_garbage_is_an_error_not_an_empty_result(self):
        """読めない出力を「結果 0 件」にしない。黙って未検査になるため。"""
        with pytest.raises(ValueError):
            jhove.parse_output(b"Exception in thread main")
        with pytest.raises(ValueError):
            jhove.parse_output(b"<other/>")


def test_missing_bundle_raises_unavailable(tmp_path, monkeypatch):
    """同梱が無いことを例外で知らせる（空の結果で返さない）。"""
    monkeypatch.setattr(jhove, "locate", lambda: None)
    with pytest.raises(jhove.JhoveUnavailable):
        jhove.validate([tmp_path / "a.tif"], tmp_path)


def test_a_java_that_does_not_start_is_unavailable(tmp_path):
    """起動できない Java（Intel Mac に arm64 版、など）を不適合と取り違えない。"""
    target = tmp_path / "a.tif"
    Image.new("L", (4, 4)).save(target)
    tool = jhove.Tool(java=tmp_path / "no-such-java", classpath=[])
    with pytest.raises(jhove.JhoveUnavailable):
        jhove.validate([target], tmp_path, tool=tool)


def test_a_missing_file_is_unavailable_not_a_crash(tmp_path):
    """検査用の写しを作れないとき、素の OSError で移管ごと止めないこと。

    2026-10-27、存在しないパスを渡して FileNotFoundError がそのまま
    抜けるのを確かめた。呼び出し側は JhoveUnavailable だけを受け止める。
    """
    tool = jhove.Tool(java=tmp_path / "java", classpath=[])
    (tmp_path / "java").write_text("")
    with pytest.raises(jhove.JhoveUnavailable):
        jhove.validate([tmp_path / "無い.tif"], tmp_path, tool=tool)


@needs_jhove
class TestBundledJhove:
    def test_pillow_tiff_is_valid(self, tmp_path):
        """アプリが作る非圧縮 TIFF（RGB / RGBA / パレット / グレー）が適合すること。

        2026-10-27 に実測した 4 種類。どれかが不適合になったら、変換の側を
        疑う（JHOVE の版を上げたときにも、ここで差が出る）。
        """
        paths = []
        for mode in ("RGB", "RGBA", "P", "L"):
            path = tmp_path / f"{mode}.tif"
            Image.new(mode, (32, 24)).save(path, format="TIFF", compression=None)
            paths.append(path)

        results, release = jhove.validate(paths, tmp_path)

        assert release == "JHOVE 1.34.0"
        assert {p.name: results[p].status for p in paths} == {
            p.name: jhove.VALID for p in paths
        }

    def test_truncated_tiff_is_not_valid(self, tmp_path):
        """途中で切れた TIFF を不適合と判定すること（開き直しより細かく理由が出る）。"""
        whole = tmp_path / "whole.tif"
        Image.new("RGB", (200, 200)).save(whole, format="TIFF", compression=None)
        cut = tmp_path / "cut.tif"
        cut.write_bytes(whole.read_bytes()[:30000])

        results, _ = jhove.validate([cut], tmp_path)
        assert results[cut].conforms is False
        assert results[cut].messages

    def test_japanese_file_names(self, tmp_path):
        """日本語・空白を含む名前でも検査できること。

        Windows の Java は引数を ANSI コードページで受けるので、名前は
        JHOVE に渡さず、短い ASCII の名前のハードリンクで渡している。
        その仕組みが崩れていないかを見る。
        """
        path = tmp_path / "写真 一枚目.tif"
        Image.new("RGB", (8, 8)).save(path, format="TIFF", compression=None)
        results, _ = jhove.validate([path], tmp_path)
        assert results[path].status == jhove.VALID

    def test_pdf(self, tmp_path):
        """PDF も検査できること（Ghostscript の規則が作る派生物）。"""
        from pypdf import PdfWriter

        path = tmp_path / "a.pdf"
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        with path.open("wb") as out:
            writer.write(out)

        results, _ = jhove.validate([path], tmp_path)
        assert results[path].conforms is True
        assert results[path].module.startswith("PDF-hul")

    def test_leaves_nothing_behind(self, tmp_path):
        """作業用のハードリンクを残さないこと。

        作業場所は変換の作業ディレクトリの中に作る。残すと、次の工程が
        それを拾うおそれがある。
        """
        path = tmp_path / "a.tif"
        Image.new("L", (4, 4)).save(path, format="TIFF", compression=None)
        before = sorted(p.name for p in tmp_path.iterdir())
        jhove.validate([path], tmp_path)
        assert sorted(p.name for p in tmp_path.iterdir()) == before

    def test_other_extensions_are_left_out(self, tmp_path):
        """検査部品の無い形式は結果に含めない（呼び出し側が未検査と記録する）。"""
        path = tmp_path / "a.jp2"
        path.write_bytes(b"x")
        results, _ = jhove.validate([path], tmp_path)
        assert results == {}
