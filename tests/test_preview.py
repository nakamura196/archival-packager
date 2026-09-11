"""生成物の中身を見せるための読み取りと色分け。

表示の見た目は人が判断するものだが、**どこまで読むか・何をバイナリとみなすか・
どこで色を変えるか**は規則が決まっている。ここを固定しておけば、画面を
作り替えても中身の扱いは変わらない。
"""

from __future__ import annotations

from pathlib import Path

from archival_packager.core import preview


class TestRead:
    def test_utf8_text(self, tmp_path: Path):
        p = tmp_path / "a.txt"
        p.write_text("日本語のテキスト", encoding="utf-8")
        out = preview.read(p)
        assert isinstance(out, preview.TextPreview)
        assert out.text == "日本語のテキスト"
        assert not out.truncated

    def test_shift_jis_is_still_readable(self, tmp_path: Path):
        """日本の現場では Shift_JIS の資料が普通にある。読めるなら読む。"""
        p = tmp_path / "sjis.txt"
        p.write_bytes("移管記録".encode("cp932"))
        out = preview.read(p)
        assert isinstance(out, preview.TextPreview)
        assert out.text == "移管記録"

    def test_nul_means_binary(self, tmp_path: Path):
        """NUL を含むものはバイナリ。テキストとして出すと画面が壊れる。"""
        p = tmp_path / "a.bin"
        p.write_bytes(b"PK\x03\x04\x00\x00abc")
        out = preview.read(p)
        assert isinstance(out, preview.BinaryPreview)
        assert out.size_bytes == 9
        assert out.head_hex.startswith("50 4b 03 04")

    def test_empty_file_is_binary_like(self, tmp_path: Path):
        p = tmp_path / "empty"
        p.write_bytes(b"")
        assert isinstance(preview.read(p), preview.BinaryPreview)

    def test_large_file_is_truncated(self, tmp_path: Path):
        """METS は大きな移管で数 MB になる。全部読んでも画面では追えない。"""
        p = tmp_path / "big.xml"
        p.write_text("a" * (preview.MAX_BYTES + 100), encoding="utf-8")
        out = preview.read(p)
        assert isinstance(out, preview.TextPreview)
        assert out.truncated
        assert len(out.text) == preview.MAX_BYTES


class TestHighlightXML:
    def _roles(self, source: str) -> list[tuple[str, str]]:
        return preview.highlight_xml(source)

    def test_nothing_is_lost(self):
        """色分けしても文字は 1 つも増減しないこと。ここが崩れると内容が変わる。"""
        source = '<?xml version="1.0"?>\n<a b="c">本文 &amp; 記号</a>\n<!-- 注 -->'
        assert "".join(s for s, _ in self._roles(source)) == source

    def test_element_and_attribute_are_separated(self):
        out = self._roles('<mets:file ID="f1">')
        roles = dict((s, r) for s, r in out)
        assert roles["mets:file"] == preview.ELEMENT
        assert roles["ID"] == preview.ATTRIBUTE
        assert roles['"f1"'] == preview.VALUE

    def test_comment_is_one_piece(self):
        out = self._roles("<!-- これは注 --><a/>")
        assert out[0] == ("<!-- これは注 -->", preview.COMMENT)

    def test_text_outside_tags(self):
        out = self._roles("<a>ほんぶん</a>")
        assert ("ほんぶん", preview.TEXT) in out

    def test_unclosed_tag_does_not_hang(self):
        """壊れた XML でも止まらないこと。生成物が壊れている場合に見たいのは中身。"""
        assert "".join(s for s, _ in self._roles("<a b=")) == "<a b="


class TestWalk:
    def _tree(self, root: Path) -> None:
        (root / "objects").mkdir()
        (root / "objects" / "b.txt").write_text("b", encoding="utf-8")
        (root / "objects" / "a.txt").write_text("a", encoding="utf-8")
        (root / "metadata").mkdir()
        (root / "metadata" / "report.txt").write_text("r", encoding="utf-8")
        (root / ".DS_Store").write_text("x", encoding="utf-8")

    def test_directories_come_first_then_names(self, tmp_path: Path):
        self._tree(tmp_path)
        names = [e.name for e in preview.walk(tmp_path)]
        assert names[0] == tmp_path.name
        assert names.index("metadata") < names.index("objects")
        assert names.index("a.txt") < names.index("b.txt")

    def test_environment_files_are_hidden(self, tmp_path: Path):
        """.DS_Store は中身ではなく環境が作るもの。見せても意味がない。"""
        self._tree(tmp_path)
        assert ".DS_Store" not in [e.name for e in preview.walk(tmp_path)]

    def test_depth_reflects_nesting(self, tmp_path: Path):
        self._tree(tmp_path)
        by_name = {e.name: e for e in preview.walk(tmp_path)}
        assert by_name[tmp_path.name].depth == 0
        assert by_name["objects"].depth == 1
        assert by_name["a.txt"].depth == 2

    def test_unreadable_directory_does_not_stop_the_walk(self, tmp_path: Path):
        """権限の無いフォルダがあっても、見えるところは見せる。"""
        (tmp_path / "ok.txt").write_text("x", encoding="utf-8")
        entries = preview.walk(tmp_path)
        assert any(e.name == "ok.txt" for e in entries)
