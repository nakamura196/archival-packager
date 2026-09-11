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
            path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
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
