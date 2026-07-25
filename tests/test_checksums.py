"""SHA-256 計算のテスト。

大きなファイルをチャンク読みする実装なので、チャンク境界をまたぐケースを
明示的に固定する（境界のオフバイワンは通常のテストでは見つからない）。
"""

from __future__ import annotations

import hashlib

import pytest

from archival_packager.core import checksums
from archival_packager.core.models import SIPPipelineError


def test_known_vector(tmp_path):
    """"abc" の SHA-256 は広く知られた既知値。実装のすり替わりを検出する。"""
    f = tmp_path / "a.txt"
    f.write_bytes(b"abc")
    assert (
        checksums.sha256_of(f)
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_empty_file(tmp_path):
    f = tmp_path / "empty"
    f.write_bytes(b"")
    assert (
        checksums.sha256_of(f)
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


@pytest.mark.parametrize(
    "size",
    [
        checksums.CHUNK_SIZE - 1,
        checksums.CHUNK_SIZE,
        checksums.CHUNK_SIZE + 1,
        checksums.CHUNK_SIZE * 2 + 12345,
    ],
)
def test_chunk_boundaries(tmp_path, size):
    """チャンク境界の前後でハッシュがずれないこと。"""
    data = bytes((i * 31 + 7) % 256 for i in range(size))
    f = tmp_path / f"blob-{size}"
    f.write_bytes(data)
    assert checksums.sha256_of(f) == hashlib.sha256(data).hexdigest()


def test_lowercase_hex(tmp_path):
    """マニフェストの表記ゆれを防ぐため小文字 16 進で固定する。"""
    f = tmp_path / "a.bin"
    f.write_bytes(b"\xff\xfe\xfd")
    digest = checksums.sha256_of(f)
    assert digest == digest.lower()
    assert len(digest) == 64


def test_missing_file_raises_pipeline_error(tmp_path):
    """OSError をそのまま投げず、UI に出せるメッセージへ変換する。"""
    with pytest.raises(SIPPipelineError) as exc:
        checksums.sha256_of(tmp_path / "does-not-exist")
    assert "読み取りに失敗" in exc.value.message
