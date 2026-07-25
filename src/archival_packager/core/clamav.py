"""ウイルス検査（ClamAV）。

現行 Swift 実装の `Sources/SIP/ClamAV.swift` に対応する。

同梱の `clamscan` と、取得済みの定義 DB があるときだけ実行する。
どちらも無ければスキップする（検査できないことと、検査して検出なしだったことは
report 上で区別する。前者を「安全」と読み違えられては困る）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import bundled
from .models import SIPPipelineError

# 定義 DB を置く場所。アプリ本体（読み取り専用の .app / Program Files）には書けないので
# ユーザ領域に置く。freshclam で更新する。
_DB_DIR_NAME = "archival-packager/clamav-db"

# clamscan が定義 DB として認識するファイル。どれか 1 つでもあれば実行を試みる。
_DB_FILES = ("main.cvd", "main.cld", "daily.cvd", "daily.cld", "bytecode.cvd", "bytecode.cld")


def find_tool() -> Path | None:
    return bundled.find("clamscan")


def database_directory() -> Path:
    """定義 DB の置き場所。

    Windows は %LOCALAPPDATA%、macOS/Linux は XDG に倣う。
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / _DB_DIR_NAME


def has_database(directory: Path | None = None) -> bool:
    directory = directory or database_directory()
    if not directory.is_dir():
        return False
    return any((directory / name).is_file() for name in _DB_FILES)


def scan(root: Path, *, tool: Path | None = None, database: Path | None = None) -> dict[str, str]:
    """root 配下を検査し、{絶対パス: シグネチャ名} を返す。検出が無ければ空。"""
    tool = tool or find_tool()
    if tool is None:
        raise SIPPipelineError.tool_not_found("clamscan")
    bundled.ensure_executable(tool)

    db = database or database_directory()

    args = [
        str(tool),
        f"--database={db}",
        "--recursive",
        "--infected",  # 検出したものだけ出す
        "--no-summary",
        str(root),
    ]

    try:
        proc = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SIPPipelineError.tool_failed("clamscan", -1, str(exc)) from exc

    # clamscan の終了コード: 0 = 検出なし, 1 = 検出あり, 2 以上 = エラー。
    # 1 は「正常に動いて見つかった」なので失敗として扱わない。
    if proc.returncode >= 2:
        raise SIPPipelineError.tool_failed("clamscan", proc.returncode, (proc.stderr or "")[:500])

    findings: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        # 形式: "<path>: <signature> FOUND"
        if not line.endswith(" FOUND"):
            continue
        body = line[: -len(" FOUND")]
        path, _, signature = body.rpartition(": ")
        if path and signature:
            findings[path] = signature
    return findings
