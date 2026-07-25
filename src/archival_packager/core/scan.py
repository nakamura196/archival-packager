"""入力ツリーの走査。

現行 Swift 実装の `Sources/SIP/Orchestrator.swift` の `scan` に対応する。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import ScannedFile, SIPPipelineError

# 走査から除外するファイル名。資料ではなく OS が勝手に作るもの。
_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def scan(root: Path) -> list[ScannedFile]:
    """root 配下の通常ファイルを列挙する。

    シンボリックリンクは辿らない。辿ると (a) 入力ツリーの外にあるファイルを
    受入対象に含めてしまい、(b) 循環リンクで無限走査になる。
    """
    if not root.is_dir():
        raise SIPPipelineError.io(f"入力フォルダを走査できません: {root}")

    # root 自体は解決しておく。macOS では /var が /private/var への
    # シンボリックリンクであり、これを揃えないと相対パス化が崩れる
    # （zip 展開先の一時ディレクトリで実際に問題になった）。
    resolved_root = root.resolve()

    out: list[ScannedFile] = []
    for path in resolved_root.rglob("*"):
        if path.name in _EXCLUDED_NAMES:
            continue
        if path.is_symlink() or not path.is_file():
            continue

        stat = path.stat()
        out.append(
            ScannedFile(
                relative_path=path.relative_to(resolved_root).as_posix(),
                absolute_path=path,
                size_bytes=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
            )
        )

    # 並び順を固定する。走査順に任せると同じ入力から違うマニフェスト・METS が出る。
    out.sort(key=lambda f: f.relative_path)
    return out


def has_objects_dir(root: Path) -> bool:
    """入力直下に objects/ があるか。

    あれば「事前確定モード」（その構造を尊重し objects/ の中身だけを payload にする）、
    無ければ「自動ラップモード」（中身を objects/ に写す）。
    Archivematica の transfer 構造をそのまま受け取れるようにするための判定。
    """
    return (root / "objects").is_dir()
