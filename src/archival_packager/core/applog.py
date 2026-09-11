"""落ちたときの記録。

利用者は情報システムの専門家ではない。画面に出た文字を書き写してもらうのは
現実的でないので、**ファイルに残して「これを送ってください」と言える形**にする。

2026-09-11 に配布版で 2 件の不具合が出た際、エラーの本文を手で貼っていただいて
初めて原因が分かった。同じことを利用者に求めないための仕組み。

置き場所はウイルス定義 DB と同じ作法に揃える
（Windows は %LOCALAPPDATA%、macOS/Linux は XDG）。
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime
from pathlib import Path

_DIR_NAME = "ArchivalPackager"
_FILE_NAME = "errors.log"
_MAX_BYTES = 1_000_000  # これを超えたら作り直す。際限なく太らせない。


def log_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / _DIR_NAME / _FILE_NAME


def environment() -> str:
    """どの環境で起きたか。報告に必ず要るので、記録にも画面にも同じものを出す。"""
    from archival_packager import __version__ as version

    return (
        f"Archival Packager {version} / {platform.system()} {platform.release()} "
        f"({platform.machine()}) / Python {sys.version.split()[0]}"
    )


def record(summary: str, detail: str = "") -> Path | None:
    """1 件書き足す。書けなくても呼び出し元は止めない。

    記録できないこと自体は利用者の作業を妨げないので、例外を投げ返さない。
    """
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > _MAX_BYTES:
            path.unlink()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} =====\n")
            f.write(environment() + "\n")
            f.write(summary + "\n")
            if detail:
                f.write(detail.rstrip() + "\n")
        return path
    except OSError:
        return None
