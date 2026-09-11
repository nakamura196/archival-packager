"""ウイルス検査（ClamAV）。

現行 Swift 実装の `Sources/SIP/ClamAV.swift` に対応する。

同梱の `clamscan` と、取得済みの定義 DB があるときだけ実行する。
どちらも無ければスキップする（検査できないことと、検査して検出なしだったことは
report 上で区別する。前者を「安全」と読み違えられては困る）。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from datetime import datetime
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


def find_updater() -> Path | None:
    """定義 DB を取得/更新する freshclam。"""
    return bundled.find("freshclam")


def certs_directory() -> Path | None:
    """CVD（定義 DB）の署名検証に使う root CA の置き場所。

    ClamAV 1.4 以降、clamscan / freshclam はここを見つけられないと起動しない。
    探索先の既定はビルド時に焼き込まれた絶対パス（/usr/local/clamav/etc/certs）で、
    ビルドした機械にしか存在しない。同梱したものを明示的に渡す必要がある。
    siegfried が default.sig を見失うのと同じ性質の問題で、開発機に ClamAV を
    入れていると「動いてしまう」ので気づきにくい。
    """
    tool = find_tool() or find_updater()
    if tool is None:
        return None
    certs = tool.parent / "clamav-certs"
    return certs if certs.is_dir() else None


def _tool_environment() -> dict[str, str] | None:
    """証明書の場所を環境変数で渡す。

    コマンドライン引数 `--cvdcertsdir` では足りない。freshclam は取得した
    定義 DB を読み込んでテストする段で libclamav を通すが、そこは引数を見ず
    焼き込まれた既定パスを使うため、取得自体は成功してもテストで失敗する:

        Failed to load new database: Broken or not a CVD file
        Invalid certs directory '/usr/local/clamav/etc/certs'

    環境変数 CVD_CERTS_DIR はその内側まで効く。
    """
    certs = certs_directory()
    if certs is None:
        return None
    env = dict(os.environ)
    env["CVD_CERTS_DIR"] = str(certs)
    return env


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


def database_status(directory: Path | None = None) -> str:
    """UI に出す 1 行。取得済みかどうかと、いつ更新したかを返す。

    「未取得」と「取得済みだが古い」は別のことなので、日付まで見せる。
    検査をスキップしたのに「ウイルスなし」と読まれるのが一番まずい。
    """
    directory = directory or database_directory()
    present = [directory / name for name in _DB_FILES if (directory / name).is_file()]
    if not present:
        return "ウイルス定義: 未取得（検査はスキップされます）"

    newest = max(p.stat().st_mtime for p in present)
    when = datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
    return f"ウイルス定義: 取得済み（{len(present)} ファイル / 更新 {when}）"


def update_database(
    *,
    updater: Path | None = None,
    directory: Path | None = None,
    progress: Callable[[str], None] = lambda _msg: None,
) -> None:
    """同梱 freshclam で定義 DB を取得/更新する。

    freshclam は既定でシステムの設定ファイル（/usr/local/etc/freshclam.conf 等）を
    読もうとし、無ければエラーで止まる。配布先の環境設定に依存したくないので、
    最小構成の設定ファイルを自前で書いて明示的に渡す。システム側の設定には触れない。

    進捗行は逐次 progress に流す。数十 MB のダウンロードなので、
    黙って固まったように見えないようにする。
    """
    updater = updater or find_updater()
    if updater is None:
        raise SIPPipelineError.tool_not_found("freshclam（未同梱）")
    bundled.ensure_executable(updater)

    directory = directory or database_directory()
    directory.mkdir(parents=True, exist_ok=True)

    conf = directory.parent / "freshclam.conf"
    conf.write_text(
        f"DatabaseDirectory {directory}\nDatabaseMirror database.clamav.net\n",
        encoding="utf-8",
    )

    args = [str(updater), f"--config-file={conf}", f"--datadir={directory}"]
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
            env=_tool_environment(),
            **bundled.no_window(),
        )
    except OSError as exc:
        raise SIPPipelineError.tool_failed("freshclam", -1, str(exc)) from exc

    assert proc.stdout is not None
    with proc.stdout as stream:
        for line in stream:
            if line := line.strip():
                progress(line)

    if proc.wait() != 0:
        raise SIPPipelineError.tool_failed(
            "freshclam", proc.returncode, "定義の取得に失敗しました（ネットワーク/ミラーを確認してください）"
        )


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
        proc = subprocess.run(
            args, capture_output=True, encoding="utf-8", errors="replace",
            env=_tool_environment(),
            **bundled.no_window(),
        )
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
