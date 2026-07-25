"""チェックサム計算。

現行 Swift 実装の `Sources/SIP/Checksums.swift` に対応する。
大きなファイルでもメモリ消費が一定になるよう、チャンク読み込みで
SHA-256 をストリーム計算する（Swift 版は CryptoKit、こちらは hashlib）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .models import SIPPipelineError

# 1 回に読むチャンクサイズ（1 MiB）。Swift 版と揃えてある。
CHUNK_SIZE = 1 << 20


def sha256_of(path: Path) -> str:
    """ファイルの SHA-256 を小文字 16 進文字列で返す。"""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                hasher.update(chunk)
    except OSError as exc:
        raise SIPPipelineError.io(f"読み取りに失敗: {path} ({exc})") from exc
    return hasher.hexdigest()
