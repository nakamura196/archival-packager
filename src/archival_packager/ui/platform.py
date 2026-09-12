"""OS 依存の小さな操作。

Swift 版は AppKit（NSWorkspace）を直接呼んでいた。Windows でも動かす必要が
あるので、OS 差はここに閉じ込める。UI の他の部分からは OS を意識しない。
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"


def reveal_in_file_manager(path: Path) -> None:
    """ファイルマネージャで対象を表示する（macOS: Finder / Windows: エクスプローラ）。

    成果物ができた直後に「どこにあるか」を示すのは、この種のツールで
    最も使われる操作。失敗しても例外は投げない（本処理は既に終わっている）。
    """
    try:
        if IS_MACOS:
            subprocess.run(["open", "-R", str(path)], check=False)
        elif IS_WINDOWS:
            # /select, は対象を選択して開く。カンマの後に空白を入れてはいけない。
            subprocess.run(["explorer", f"/select,{path}"], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except OSError:
        pass


def open_path(path: Path) -> None:
    """既定のアプリで開く（report.html をブラウザで開く用）。"""
    try:
        if IS_MACOS:
            subprocess.run(["open", str(path)], check=False)
        elif IS_WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass
