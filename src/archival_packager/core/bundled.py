"""同梱バイナリの解決。

siegfried(sf) / clamscan / gs / magick といった外部ツールは、開発時と
パッケージ後で置き場所が変わる。その差をここに閉じ込める。

## 配置の根拠（spike/README.md の実測結果）

**バイナリを Python アプリ側（app.zip に入る場所）に置いてはいけない。**
起動自体はできるが、公証で必ず弾かれる。Apple の審査は app.zip の中まで
降りて検査し、Developer ID 署名・セキュアタイムスタンプ・hardened runtime を
要求するが、zip 内のデータは署名できないため原理的に通せない。

したがってバンドル内に置き、個別に署名する。

    macOS    <App>.app/Contents/Resources/bin/
             sys.executable = <App>.app/Contents/MacOS/<exe> なので
             parent.parent / "Resources" / "bin"

    Windows  <exe と同じ階層>/bin/

    開発時    リポジトリ直下 binaries/<os>/

## 実行ビット

macOS のパッケージ経路では実行ビットが保たれるが、配布物の展開経路や
zip を経由した配置では落ちることがある（spike で実測）。起動前に必ず
確認し、必要なら付与する。付与できなければ起動は失敗するので、
そのときは呼び出し側にエラーを返す。
"""

from __future__ import annotations

import os
import platform
import stat
import sys
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"


def _exe_name(name: str) -> str:
    return f"{name}.exe" if IS_WINDOWS else name


def bin_dir() -> Path | None:
    """同梱バイナリの置き場所を返す。見つからなければ None。"""
    for candidate in _candidate_dirs():
        if candidate.is_dir():
            return candidate
    return None


def _candidate_dirs() -> list[Path]:
    exe = Path(sys.executable).resolve()
    candidates: list[Path] = []

    if IS_WINDOWS:
        candidates.append(exe.parent / "bin")
    else:
        # <App>.app/Contents/MacOS/<exe> -> <App>.app/Contents/Resources/bin
        candidates.append(exe.parent.parent / "Resources" / "bin")

    # 開発モード（flet run / pytest）。src/archival_packager/core/ から 3 つ上がリポジトリ直下。
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "binaries" / ("windows" if IS_WINDOWS else "macos"))

    return candidates


def find(name: str) -> Path | None:
    """同梱ツールの実行ファイルを返す。見つからなければ None。

    Args:
        name: 拡張子を含まないツール名（例: "sf"）。Windows では .exe を補う。
    """
    directory = bin_dir()
    if directory is None:
        return None
    tool = directory / _exe_name(name)
    return tool if tool.is_file() else None


def ensure_executable(tool: Path) -> None:
    """実行ビットが落ちていれば付け直す。

    Windows にはパーミッションの概念が無いので何もしない。
    付与に失敗した場合は例外を伝播させる（黙って起動失敗させるより、
    原因が分かる形で落とす方がよい）。
    """
    if IS_WINDOWS:
        return
    if os.access(tool, os.X_OK):
        return
    mode = tool.stat().st_mode
    tool.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def signature_home() -> Path | None:
    """siegfried のシグネチャ home（default.sig を置いたディレクトリ）を返す。

    siegfried は指定が無いと OS 既定の場所を探しに行く。開発機に Homebrew 版の
    siegfried が入っていると、そちらの default.sig を拾って動いてしまい、
    「開発機では動くが配布先では識別できない」という気づきにくい欠陥になる
    （spike で実際に踏んだ）。必ず同梱したものを明示的に渡すこと。
    """
    directory = bin_dir()
    if directory is None:
        return None
    return directory if (directory / "default.sig").is_file() else None
