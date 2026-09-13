"""テスト全体で使う道具。

**テストは Windows / macOS / Linux のいずれでも通ること。**
2026-09-11、Windows で pytest を回し始めたところ 6 件が落ちた。いずれも
POSIX を前提に書かれていたもので、アプリの不具合ではなかった。
移植できていないテストは、その OS での不具合を隠す。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _japanese_interface():
    """画面の言語を既定（日本語）に固定する。

    `i18n` は利用者の設定ファイル（`settings.json`）から言語を読む。
    **開発機で画面を英語にしていると、UI のテストが 2 件落ちた。**
    テストは「実行」のような日本語のラベルで部品を探しているため。
    利用者の設定でテストの結果が変わってはいけないので、ここで固定する。
    """
    from archival_packager import i18n

    before = i18n.current_language()
    i18n._current = i18n.DEFAULT
    yield
    i18n._current = before


@pytest.fixture
def fake_tool(tmp_path: Path):
    """終了コードと標準エラーを指定できる、偽の外部ツールを作る。

    Windows は shebang を解さないので .bat を書く。POSIX は sh スクリプト。
    """

    def _make(
        name: str = "faketool",
        *,
        exit_code: int = 0,
        stderr: str = "",
        output_text: str = "",
    ) -> Path:
        """output_text を渡すと、2 番目の引数のパスへその文字を書く。"""
        if sys.platform == "win32":
            path = tmp_path / f"{name}.bat"
            lines = ["@echo off"]
            if stderr:
                lines.append(f"echo {stderr} 1>&2")
            if output_text:
                # %~2 で引用符を外す。> を先に置かないと echo に食われる。
                lines.append(f'> "%~2" echo {output_text}')
            lines.append(f"exit /b {exit_code}")
            # 改行は自分で決める。newline を省くと Windows で \r\n が
            # \r\r\n になる（tests/test_sip_pipeline.py の同じ罠を参照）。
            path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii", newline="")
        else:
            path = tmp_path / name
            lines = ["#!/bin/sh"]
            if stderr:
                lines.append(f"echo '{stderr}' >&2")
            if output_text:
                lines.append(f'printf %s "{output_text}" > "$2"')
            lines.append(f"exit {exit_code}")
            path.write_text("\n".join(lines) + "\n")
            path.chmod(0o755)
        return path

    return _make


# conftest は import できないので、OS で飛ばす印はテスト側にその場で書く。
#   @pytest.mark.skipif(sys.platform == "win32", reason="...")
# Windows は : * ? " < > | をファイル名に使えない。
