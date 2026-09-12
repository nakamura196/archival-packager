"""ファイル名サニタイズの単体テスト。

現行 Swift 実装の `Tests/ArchivalPackagerTests/FilenameSanitizerTests.swift` を移植したもの。
移植の正しさは既存テストで担保する方針なので、期待値は Swift 版から変えていない
（唯一の例外は Windows 予約名。Swift 版に無い処理なので新規に追加した）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from archival_packager.core import filenames
from archival_packager.core.models import ScannedFile


def sf(rel: str) -> ScannedFile:
    return ScannedFile(
        relative_path=rel,
        absolute_path=Path("/tmp") / rel,
        size_bytes=1,
        modified=datetime.fromtimestamp(0, UTC),
    )


class TestSanitizeUnit:
    def test_forbidden_replaced_and_slash_preserved(self):
        """Windows 不可文字・シェル/XML 危険文字は _ に。区切りの "/" は構造として保持。"""
        assert filenames.sanitize("a/b:c?d*e.txt", normalize_nfc=False) == "a/b_c_d_e.txt"
        assert filenames.sanitize("dir/x|y&z.txt", normalize_nfc=False) == "dir/x_y_z.txt"
        assert filenames.sanitize("q<a>[b];c.txt", normalize_nfc=False) == "q_a__b__c.txt"

    def test_japanese_and_fullwidth_preserved(self):
        """日本語・全角記号（＆ ：）は Windows でも有効なので保持する（過剰変換しない）。"""
        assert filenames.sanitize("レポート＆案.txt", normalize_nfc=False) == "レポート＆案.txt"
        assert (
            filenames.sanitize("サブ：フォルダ/メモ.txt", normalize_nfc=False)
            == "サブ：フォルダ/メモ.txt"
        )

    def test_control_chars_and_trailing_dot_space_trimmed(self):
        assert filenames.sanitize("a\tb.txt", normalize_nfc=False) == "a_b.txt"
        # Windows は末尾のドット・空白を許さない。
        assert filenames.sanitize("name . ", normalize_nfc=False) == "name"
        assert filenames.sanitize("report...", normalize_nfc=False) == "report"

    def test_nfc_normalization(self):
        """濁点が結合文字で分解された "が"（か U+304B + U+3099）は NFC ON で合成される。"""
        decomposed = "が.txt"
        assert filenames.sanitize(decomposed, normalize_nfc=True) == "が.txt"
        # OFF なら分解のまま保持。
        assert filenames.sanitize(decomposed, normalize_nfc=False) == decomposed


class TestWindowsReserved:
    """Swift 版に無い追加処理。予約名は Windows でファイルとして作成できない。"""

    @pytest.mark.parametrize("name", ["CON", "PRN", "AUX", "NUL", "COM1", "LPT9"])
    def test_bare_reserved_names_escaped(self, name):
        assert filenames.sanitize(name, normalize_nfc=False) == f"_{name}"

    def test_reserved_with_extension_escaped(self):
        """拡張子が付いていても予約は解けない（CON.txt も作成できない）。"""
        assert filenames.sanitize("CON.txt", normalize_nfc=False) == "_CON.txt"

    def test_reserved_match_is_case_insensitive(self):
        assert filenames.sanitize("con.TXT", normalize_nfc=False) == "_con.TXT"

    def test_non_reserved_lookalikes_untouched(self):
        """前方一致では判定しない。CONSOLE や CON2 は予約ではない。"""
        assert filenames.sanitize("CONSOLE.txt", normalize_nfc=False) == "CONSOLE.txt"
        assert filenames.sanitize("COM0.txt", normalize_nfc=False) == "COM0.txt"

    def test_can_be_disabled_for_parity_with_swift(self):
        """差分検証で現行 Swift 版と突合するときは無効化できる。"""
        assert (
            filenames.sanitize("CON.txt", normalize_nfc=False, windows_reserved=False)
            == "CON.txt"
        )


class TestApply:
    def test_records_original_and_count(self):
        out, renamed = filenames.apply([sf("a:b.txt"), sf("clean.txt")], normalize_nfc=True)
        assert renamed == 1, "変更されたのは 1 件"

        changed = next(f for f in out if f.original_relative_path is not None)
        assert changed.original_relative_path == "a:b.txt", "元名を保持"
        assert changed.relative_path == "a_b.txt", "安全名に置換"

        clean = next(f for f in out if f.relative_path == "clean.txt")
        assert clean.original_relative_path is None, "未変更ファイルは None"

    def test_collision_uniquified(self):
        """"a:b.txt" と "a?b.txt" は両方 "a_b.txt" に収束するので一方を一意化する。"""
        out, renamed = filenames.apply([sf("a:b.txt"), sf("a?b.txt")], normalize_nfc=True)
        assert renamed == 2

        names = {f.relative_path for f in out}
        assert len(names) == 2, "衝突しても上書きせず一意化"
        assert names == {"a_b.txt", "a_b_2.txt"}
        assert all(f.original_relative_path is not None for f in out), "両方とも元名を保持"

    def test_collision_without_extension(self):
        out, _ = filenames.apply([sf("a:b"), sf("a?b")], normalize_nfc=True)
        assert {f.relative_path for f in out} == {"a_b", "a_b_2"}

    def test_collision_keeps_directory(self):
        out, _ = filenames.apply([sf("d/a:b.txt"), sf("d/a?b.txt")], normalize_nfc=True)
        assert {f.relative_path for f in out} == {"d/a_b.txt", "d/a_b_2.txt"}

    def test_absolute_path_still_points_at_the_original_file(self):
        """sanitize しても絶対パスは元ファイルを指したままでなければコピーできない。"""
        # Windows は ":" をドライブ区切りと解釈するため、Path("/tmp/a:b.txt") が
        # 別物になる。元の ScannedFile が持っていた値と突き合わせる。
        original = sf("a:b.txt")
        out, _ = filenames.apply([original], normalize_nfc=True)
        assert out[0].absolute_path == original.absolute_path
