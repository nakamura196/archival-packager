"""siegfried の出力解釈とバイナリ解決のテスト。

JSON 解釈は siegfried を実際に起動せずに固定する（CI に sf を置かなくても
回帰を検出できるようにするため）。同梱バイナリがある環境では、実際に
起動する統合テストも走る。
"""

from __future__ import annotations

import pytest

from archival_packager.core import bundled, siegfried
from archival_packager.core.models import SIPPipelineError
from archival_packager.core.siegfried import _records_from


class TestOutputParsing:
    def test_prefers_pronom_namespace(self):
        """pronom 以外の名前空間が先に来ても pronom を選ぶ。"""
        out = _records_from(
            {
                "files": [
                    {
                        "filename": "/in/a.pdf",
                        "matches": [
                            {"ns": "loc", "id": "fdd000030", "format": "PDF (LoC)"},
                            {
                                "ns": "pronom",
                                "id": "fmt/19",
                                "format": "Acrobat PDF 1.5",
                                "mime": "application/pdf",
                                "basis": "extension match pdf; byte match",
                            },
                        ],
                    }
                ]
            }
        )
        rec = out["/in/a.pdf"]
        assert rec.puid == "fmt/19"
        assert rec.format_name == "Acrobat PDF 1.5"
        assert rec.mime_type == "application/pdf"
        assert rec.basis == "extension match pdf; byte match"

    def test_falls_back_to_first_match_when_no_pronom(self):
        out = _records_from(
            {"files": [{"filename": "/in/a.bin", "matches": [{"ns": "loc", "id": "x", "format": "F"}]}]}
        )
        assert out["/in/a.bin"].puid == "x"
        assert out["/in/a.bin"].format_name == "F"

    def test_unknown_puid_becomes_none(self):
        """"UNKNOWN" は識別できていないので PUID 無しとして扱う。"""
        out = _records_from(
            {"files": [{"filename": "/in/a", "matches": [{"ns": "pronom", "id": "UNKNOWN"}]}]}
        )
        assert out["/in/a"].puid is None

    def test_unknown_is_case_insensitive(self):
        out = _records_from(
            {"files": [{"filename": "/in/a", "matches": [{"ns": "pronom", "id": "unknown"}]}]}
        )
        assert out["/in/a"].puid is None

    def test_empty_strings_become_none(self):
        """空文字を素通しすると METS に空要素が出るので None に落とす。"""
        out = _records_from(
            {
                "files": [
                    {
                        "filename": "/in/a",
                        "matches": [{"ns": "pronom", "id": "", "format": "", "mime": "", "warning": ""}],
                    }
                ]
            }
        )
        rec = out["/in/a"]
        assert (rec.puid, rec.format_name, rec.mime_type, rec.warning) == (None, None, None, None)

    def test_warning_is_kept(self):
        """拡張子不一致は目視確認したい情報なので落とさない。"""
        out = _records_from(
            {
                "files": [
                    {
                        "filename": "/in/a.txt",
                        "matches": [{"ns": "pronom", "id": "fmt/11", "warning": "extension mismatch"}],
                    }
                ]
            }
        )
        assert out["/in/a.txt"].warning == "extension mismatch"

    def test_file_without_matches(self):
        """matches が空でもエントリ自体は残す（未識別として記録されるべき）。"""
        out = _records_from({"files": [{"filename": "/in/a", "matches": []}]})
        assert "/in/a" in out
        assert out["/in/a"].puid is None

    def test_entry_without_filename_is_skipped(self):
        out = _records_from({"files": [{"matches": [{"ns": "pronom", "id": "fmt/1"}]}]})
        assert out == {}

    @pytest.mark.parametrize("payload", [{}, {"files": None}, [], "", None])
    def test_malformed_output_does_not_raise(self, payload):
        """siegfried のバージョン差で形が変わっても落とさない。"""
        assert _records_from(payload) == {}


class TestToolResolution:
    def test_missing_tool_raises_tool_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bundled, "find", lambda name: None)
        with pytest.raises(SIPPipelineError) as exc:
            siegfried.identify(tmp_path)
        assert "見つかりません" in exc.value.message

    def test_ensure_executable_restores_bit(self, tmp_path):
        """展開経路で実行ビットが落ちても復旧できること（spike で実測した事象）。"""
        tool = tmp_path / "sf"
        tool.write_bytes(b"#!/bin/sh\necho hi\n")
        tool.chmod(0o644)
        bundled.ensure_executable(tool)
        import os

        assert os.access(tool, os.X_OK)


@pytest.mark.skipif(bundled.find("sf") is None, reason="同梱 sf が無い環境")
class TestIntegration:
    """同梱バイナリがある環境でのみ走る。実際に sf を起動する。"""

    def test_identifies_a_pdf(self, tmp_path):
        f = tmp_path / "a.pdf"
        # PRONOM の PDF シグネチャは先頭の "%PDF-" と末尾の "%%EOF" の両方を見る。
        # ヘッダだけだと byte match が成立せず、拡張子からの候補列挙どまりになる
        # （最初この形で書いてテストが落ち、それで気づいた）。
        f.write_bytes(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"trailer<</Root 1 0 R>>\n"
            b"%%EOF\n"
        )
        out = siegfried.identify(tmp_path)
        rec = next(r for path, r in out.items() if path.endswith("a.pdf"))
        assert rec.mime_type == "application/pdf", f"識別できていない: {rec}"
        assert rec.puid is not None and rec.puid.startswith("fmt/")

    def test_unidentified_file_reports_a_warning(self, tmp_path):
        """識別できない場合、PUID は None だが warning は残す（目視確認の材料になる）。

        拡張子の選定に注意。.xyz は PRONOM に登録があり（fmt/2067 XYZ Coordinate Data）
        拡張子一致してしまう。PRONOM に無い拡張子を使うこと。
        """
        (tmp_path / "mystery.qqzz9").write_bytes(b"\x00\x01\x02not-a-known-format")
        out = siegfried.identify(tmp_path)
        rec = next(r for path, r in out.items() if path.endswith("mystery.qqzz9"))
        assert rec.puid is None
        assert rec.warning, "未識別であることが分かる情報を残すこと"

    def test_extension_only_match_is_flagged(self, tmp_path):
        """拡張子だけの一致は PUID が付いても warning が立つ。

        中身が拡張子と食い違う資料は受入時に目視確認したいので、
        この情報を落とさないことが重要。
        """
        (tmp_path / "coords.xyz").write_bytes(b"\x00\x01\x02not-really-xyz-data")
        out = siegfried.identify(tmp_path)
        rec = next(r for path, r in out.items() if path.endswith("coords.xyz"))
        assert rec.basis == "extension match xyz"
        assert rec.warning == "match on extension only"

    def test_uses_the_bundled_signature_db(self):
        """同梱した default.sig を使っていること。

        指定を省くと開発機の Homebrew 版を拾ってしまい、配布先でだけ壊れる。
        """
        assert bundled.signature_home() is not None, "default.sig を同梱すること"
