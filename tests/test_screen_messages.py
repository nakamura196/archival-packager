"""core が書いた日本語を、英語の画面で訳せていること。

2026-09-23、画面マニュアルの英語版を撮ったところ、2 か所が日本語のまま出ていた。

  1. ウイルス定義の状態（「ウイルス定義: 未取得（検査はスキップされます）」）
  2. 「処理の記録」の詳細欄（「マニフェストと一致（2 件中）」「検出なし」など）

どちらも core が作る文で、core は i18n を通さない（パッケージの中身を画面の
言語で変えないため）。画面に並べる直前に訳す `ui/messages.py` に、
この 2 つの入口が無かった。

**原文は手で写さず、core に実際に作らせる。** 手で写した文で試すと、
core の言い回しが変わったときに、テストは通るのに画面は日本語に戻る。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from archival_packager import i18n
from archival_packager.core import aip_pipeline, clamav, package_report, sip_pipeline
from archival_packager.core.aip_models import AIPOptions
from archival_packager.core.models import SIPMetadata, SIPOptions
from archival_packager.ui import messages

JAPANESE = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿＀-￯]")


@pytest.fixture
def english(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "_settings_path", lambda: tmp_path / "settings.json")
    i18n._current = "en"


class TestVirusDatabaseStatus:
    def test_not_downloaded(self, tmp_path, english):
        line = messages.virus_db_status(clamav.database_status(tmp_path))
        assert not JAPANESE.search(line), line
        assert "not downloaded" in line

    def test_downloaded_keeps_the_count_and_date(self, tmp_path, english):
        (tmp_path / "main.cvd").write_bytes(b"x")
        raw = clamav.database_status(tmp_path)
        line = messages.virus_db_status(raw)
        assert not JAPANESE.search(line), line
        # 件数と日付は落とさない。「いつの定義か」が読めないと古さに気づけない。
        assert "1 files" in line
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", line)

    def test_japanese_screen_is_unchanged(self, tmp_path):
        raw = clamav.database_status(tmp_path)
        assert messages.virus_db_status(raw) == raw


@pytest.fixture
def aip_events(tmp_path: Path) -> list[package_report.Event]:
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.txt").write_text("資料 A\n", encoding="utf-8")
    (src / "写真.png").write_bytes(png)
    out = tmp_path / "sip-out"
    out.mkdir()
    sip = sip_pipeline.run(
        input_path=src, output_parent=out,
        metadata=SIPMetadata(identifier="x", title="y"),
        options=SIPOptions(), progress=lambda _m: None,
    ).sip_path
    aout = tmp_path / "aip-out"
    aout.mkdir()
    aip = aip_pipeline.run(
        sip_root=sip, output_parent=aout, options=AIPOptions(),
        progress=lambda _m: None,
    ).aip_path
    return package_report.read(aip).events


class TestEventDetails:
    def test_no_japanese_left_in_english(self, aip_events, english):
        assert aip_events
        leftover = [
            (e.event_type, messages.event_detail(e.detail))
            for e in aip_events
            # 派生物のパスには原本の名前（写真.png）がそのまま入りうる。名前は訳さない。
            if JAPANESE.search(re.sub(r"\S*写真\S*", "", messages.event_detail(e.detail)))
        ]
        assert not leftover, leftover

    def test_details_seen_in_the_manual_are_translated(self, english):
        assert messages.event_detail("マニフェストと一致（2 件中）") == \
            "Matches the manifest (2 files checked)"
        assert messages.event_detail("検出なし") == "Clean"
        assert messages.event_detail("PRONOM fmt/11（Portable Network Graphics）") == \
            "PRONOM fmt/11 (Portable Network Graphics)"

    def test_virus_name_is_kept(self, english):
        assert messages.event_detail("検出: Eicar-Signature") == "Detected: Eicar-Signature"

    def test_validation_keeps_the_path(self, english):
        line = messages.event_detail("objects/x.tif: Pillow で再読込: RGB 1x1 / 3 フレーム")
        assert line == "objects/x.tif: Reopened with Pillow: RGB 1x1, 3 frames"

    def test_normalization_keeps_the_machine_values(self, tmp_path, english):
        """変換の記録は、core の to_tiff に実際に書かせた文で確かめる。

        上の AIP 全体のテストは siegfried が無い環境（CI）では PNG を識別できず、
        変換の記録が 1 件も出ない。それで「(非圧縮)」の訳し漏れを見逃した。
        """
        import base64

        from archival_packager.core import image_normalize

        src = tmp_path / "a.png"
        src.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        ))
        note = image_normalize.to_tiff(src, tmp_path / "a.tif")
        detail = f'rule="image-to-tiff"; program="pillow"; {note}; 読み戻せなかったため破棄しました'
        line = messages.event_detail(detail)
        assert not JAPANESE.search(line), line
        assert line.startswith('rule="image-to-tiff"; program="pillow"; ')
        assert "TIFF (uncompressed)" in line

    def test_unknown_text_is_returned_as_is(self, english):
        assert messages.event_detail("未知の文") == "未知の文"

    def test_japanese_screen_is_unchanged(self, aip_events):
        for e in aip_events:
            assert messages.event_detail(e.detail) == e.detail


def _texts(control) -> list[str]:
    """組み上がった画面の Text の中身を、入れ子をたどって全部集める。"""
    import flet as ft

    found: list[str] = []
    stack = [control]
    while stack:
        c = stack.pop()
        if isinstance(c, ft.Text) and isinstance(c.value, str):
            found.append(c.value)
        for name in ("content", "controls", "rows", "cells"):
            child = getattr(c, name, None)
            if isinstance(child, list):
                stack.extend(child)
            elif child is not None and not isinstance(child, (str, int, float)):
                stack.append(child)
    return found


class TestEventAgents:
    """「処理の記録」の「実行したもの」欄。

    2026-09-24、英語の画面でもこの欄が和文の読点「、」でつながっていた。
    core は名前を「、」で連結した文字列（EventRow.agent）も持っているが、
    これはコマンドラインの出力用。画面は名前の組（agents）から、画面の言語の区切りでつなぐ。
    """

    REPORT = package_report.PackageReport(root=Path("."), overview=package_report.Overview(), events=[
        package_report.EventRow(
            event_type="format identification",
            agents=("Archival Packager", "siegfried"),
        ),
    ])

    def test_english_uses_a_comma(self, english):
        from archival_packager.ui import viewer

        texts = _texts(viewer._events_tab(self.REPORT))
        assert "Archival Packager, siegfried" in texts
        assert not any("、" in s for s in texts), texts

    def test_japanese_is_unchanged(self, tmp_path, monkeypatch):
        from archival_packager.ui import viewer

        monkeypatch.setattr(i18n, "_settings_path", lambda: tmp_path / "settings.json")
        i18n._current = "ja"
        assert "Archival Packager、siegfried" in _texts(viewer._events_tab(self.REPORT))
