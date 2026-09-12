"""完全性確認 — チェックサム・マニフェストの再ハッシュ照合。

退役した Swift 実装の `Sources/AIP/Fixity.swift` に由来する。

入力 SIP のマニフェストを読み、ペイロード各ファイルを実際に SHA-256 で
再計算して一致を確かめる。不一致や読めないファイルがあれば相対パスを返す。

マニフェストの位置と行のパス基準:

    bag    <sipRoot>/manifest-sha256.txt
           行 "<hash>  data/objects/..."（bag ルート基準）
    plain  <sipRoot>/metadata/submissionDocumentation/checksum.sha256
           行 "<hash>  objects/..."
"""

from __future__ import annotations

import re
from pathlib import Path

from .aip_models import FixityStatus
from .checksums import sha256_of

# ハッシュとパスの区切り。BagIt 慣例では空白 2 個だが、タブや 1 個の実装もあるため
# 「1 個以上の空白類」で切る。
_SEPARATOR = re.compile(r"\s+")

PLAIN_MANIFEST = Path("metadata") / "submissionDocumentation" / "checksum.sha256"
BAG_MANIFEST = Path("manifest-sha256.txt")


def verify(sip_root: Path, *, is_bag: bool) -> FixityStatus:
    manifest = sip_root / (BAG_MANIFEST if is_bag else PLAIN_MANIFEST)

    if not manifest.is_file():
        return FixityStatus.skipped(f"マニフェストが見つかりません: {manifest.name}")

    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        return FixityStatus.skipped(f"マニフェストを読めません: {exc}")

    checked = 0
    mismatches: list[str] = []

    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        expected, rel_path = parsed

        target = sip_root / rel_path
        if not target.is_file():
            mismatches.append(f"{rel_path}（ファイル無し）")
            continue

        checked += 1
        try:
            actual = sha256_of(target)
        except Exception:
            # 読めなかったファイルは不一致として扱う。黙って通すと
            # 「照合済み」の記録だけが残ってしまう。
            mismatches.append(f"{rel_path}（読み取り失敗）")
            continue

        if actual.lower() != expected.lower():
            mismatches.append(rel_path)

    # 不一致があるなら、照合できた件数が 0 でも failed。
    #
    # Swift 版はここで checked == 0 を先に見て skipped を返していた。
    # そのため「マニフェストに記載されたファイルが全て存在しない」SIP に対して
    # 「マニフェストに有効な行がありません」= 検査せず飛ばした、と報告してしまう。
    # ファイルが消えている SIP を skipped と報告するのは、保存ツールとして
    # 最も避けたい種類の誤りなので、順序を入れ替えて failed を優先する。
    if mismatches:
        return FixityStatus.failed(mismatches)
    if checked == 0:
        return FixityStatus.skipped("マニフェストに有効な行がありません")
    return FixityStatus.passed(checked)


def _parse_line(line: str) -> tuple[str, str] | None:
    """"<hash><空白>+<相対パス>" を (hash, path) に分解する。

    先頭の "*"（md5deep 等が付けるバイナリ印）は除く。
    """
    trimmed = line.strip()
    if not trimmed:
        return None

    match = _SEPARATOR.search(trimmed)
    if match is None:
        return None

    digest = trimmed[: match.start()]
    path = trimmed[match.end() :].lstrip("*")

    if not digest or not path:
        return None
    return digest, path
