"""受入時のファイル名サニタイズ。

退役した Swift 実装の `Sources/SIP/FilenameSanitizer.swift` に由来する。

既定では呼ばれない（`SIPOptions.sanitize_filenames` が ON のときだけ適用）。
OFF のときは日本語・特殊文字をそのまま保持する（「日本語のまま出力したい」要望への対応）。

元のファイル名は失わない。変更したファイルは `original_relative_path` に元パスを保持し、
accession.csv の「原パス（受入時）」に記録される。SIP 内の sanitize 後ファイルとは
SHA-256 で突合できる。

## Swift 版からの意図的な差分

Swift 版は macOS 専用だったため Windows の**予約名**を考慮していない。
`CON.txt` や `PRN` はどこにも作成できず、配布先で SIP の展開が失敗する。
sanitize の目的が「どの OS でも安全な名前にすること」である以上、
これは同じ趣旨の欠落とみなして対応する（`windows_reserved` の既定は True）。

当時の差分検証で Swift 版と食い違ったのは、予約名を含む入力を与えたときだけだった。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from .models import ScannedFile

# Windows 不可文字（\ : * ? " < > |）＋ シェル/XML で危険な文字（& ; [ ]）。
# パス区切りの "/" は構造として残すため、構成要素ごとに処理する。
_FORBIDDEN = set('\\:*?"<>|&;[]')

# Windows の予約デバイス名。拡張子の有無を問わず、ステム部分が一致すると使えない
# （CON, CON.txt, con.TXT すべて不可）。
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize(path: str, *, normalize_nfc: bool, windows_reserved: bool = True) -> str:
    """パスの各構成要素をサニタイズして返す。

    Args:
        path: 入力ルートからの相対パス（区切りは "/"）。
        normalize_nfc: Unicode を NFC へ正規化するか
            （Shift_JIS 由来の濁点分解などを統一する）。
        windows_reserved: Windows 予約名を回避するか。Swift 版には無い処理。
    """
    return "/".join(
        _sanitize_component(c, normalize_nfc=normalize_nfc, windows_reserved=windows_reserved)
        for c in path.split("/")
    )


def _sanitize_component(name: str, *, normalize_nfc: bool, windows_reserved: bool) -> str:
    if not name:
        return name

    src = unicodedata.normalize("NFC", name) if normalize_nfc else name

    # 禁止文字と制御文字を "_" に。日本語や全角記号（＆ ：）は Windows でも
    # 有効なので置換しない（過剰変換しないこと自体が要件）。
    out = "".join("_" if (ch in _FORBIDDEN or _is_control(ch)) else ch for ch in src)

    # Windows は末尾のドット・空白を許さない。
    out = out.strip(" \t")
    while out.endswith("."):
        out = out[:-1]
    out = out.strip(" \t")

    if not out:
        return "_"

    if windows_reserved:
        out = _avoid_reserved(out)

    return out


def _is_control(ch: str) -> bool:
    # unicodedata のカテゴリ Cc（制御文字）。タブや改行を含む。
    return unicodedata.category(ch) == "Cc"


def _avoid_reserved(name: str) -> str:
    """Windows 予約名なら先頭にアンダースコアを足して回避する。

    判定はステム（最初のドットより前）で行う。`CON.txt` も予約扱いになるため。
    """
    stem = name.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED:
        return f"_{name}"
    return name


def apply(
    files: list[ScannedFile],
    *,
    normalize_nfc: bool,
    windows_reserved: bool = True,
) -> tuple[list[ScannedFile], int]:
    """ファイル集合に適用する。

    変更があったものは `original_relative_path` に元パスを保持する。
    sanitize 後に名前が衝突したら、拡張子の手前に "_2", "_3" … を付けて一意化する。

    Returns:
        (適用後のファイル集合, 名前が変わった件数)
    """
    used: set[str] = set()
    out: list[ScannedFile] = []
    renamed = 0

    for f in files:
        original = f.relative_path
        sanitized = sanitize(
            original, normalize_nfc=normalize_nfc, windows_reserved=windows_reserved
        )
        final = _unique_path(sanitized, used)
        if final != original:
            out.append(replace(f, relative_path=final, original_relative_path=original))
            renamed += 1
        else:
            out.append(f)

    return out, renamed


def _unique_path(path: str, used: set[str]) -> str:
    """既に使われたパスなら、最終構成要素の拡張子手前に _n を挿入して一意化する。"""
    if path not in used:
        used.add(path)
        return path

    comps = path.split("/")
    directory = "/".join(comps[:-1])
    last = comps[-1]

    stem, dot, ext = last.rpartition(".")
    if not dot:  # 拡張子が無い
        stem, ext = last, ""

    n = 2
    while True:
        name = f"{stem}_{n}" if not ext else f"{stem}_{n}.{ext}"
        candidate = f"{directory}/{name}" if directory else name
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


# 参考: Windows の MAX_PATH (260) 対策は sanitize では扱わない。
# パス長は「入力ルートからの相対パス」だけでは決まらず、ユーザが選ぶ出力先の
# 深さに依存するため、SIP を書き出す段（SIPBuilder）で全体長を検査して警告する。
_LONG_PATH_THRESHOLD = 240
_ILLEGAL_TRAILING = re.compile(r"[ .]+$")
