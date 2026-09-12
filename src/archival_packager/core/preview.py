"""生成物の中身を見せるための読み取りと色分け。

退役した Swift 実装（`nakamura196/archival-packager-swift`）の ResultViewer.swift に由来する。
**画面から切り離してある。** 表示の良し悪しは人が見て判断するものだが、
「どこまで読むか」「何をバイナリとみなすか」「どこで色を変えるか」は
規則が決まっており、機械で確かめられる。

外部ライブラリは使わない。配布物を太らせたくないのと、同梱している
ものと揃えるため（Swift 版も自前で書いていた）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: 読み込む上限。METS は大きな移管で数 MB になる。全部読んでも
#: 画面では追えないので、先頭だけ見せて「続きがある」と伝える。
MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class TextPreview:
    """テキストとして読めたもの。"""

    text: str
    truncated: bool


@dataclass(frozen=True)
class BinaryPreview:
    """テキストとして読めなかったもの。"""

    size_bytes: int
    head_hex: str


def read(path: Path) -> TextPreview | BinaryPreview:
    """中身を読む。テキストとして読めなければバイナリとして扱う。

    NUL を含むものはバイナリとみなす。テキストのつもりで出すと画面が壊れる。
    """
    size = path.stat().st_size
    with path.open("rb") as f:
        data = f.read(MAX_BYTES)
    if not data or b"\x00" in data:
        return BinaryPreview(size_bytes=size, head_hex=" ".join(f"{b:02x}" for b in data[:8]))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # 日本の現場では Shift_JIS の資料が普通にある。読めるなら読む。
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            return BinaryPreview(
                size_bytes=size, head_hex=" ".join(f"{b:02x}" for b in data[:8])
            )
    return TextPreview(text=text, truncated=size > len(data))


# --------------------------------------------------------------------------
# XML の色分け
# --------------------------------------------------------------------------

#: 色の役割。実際の色は画面側が決める（配色は表示の領分）。
TEXT = "text"
MARKUP = "markup"      # < </ > ?> などの記号
ELEMENT = "element"    # 要素名
ATTRIBUTE = "attribute"  # 属性名
VALUE = "value"        # 属性値
COMMENT = "comment"


def highlight_xml(source: str) -> list[tuple[str, str]]:
    """(文字列, 役割) の並びに分ける。

    Swift 版と同じ規則。タグの外は地の文、タグの中は要素名・属性名・属性値に
    分ける。コメントは丸ごと 1 つ。
    """
    out: list[tuple[str, str]] = []

    def add(s: str, role: str) -> None:
        if s:
            out.append((s, role))

    def add_tag(tag: str) -> None:
        i, n = 0, len(tag)

        def take(pred) -> str:
            nonlocal i
            start = i
            while i < n and pred(tag[i]):
                i += 1
            return tag[start:i]

        add(take(lambda c: c in "</?"), MARKUP)
        add(take(lambda c: not c.isspace() and c not in ">/"), ELEMENT)
        while i < n:
            add(take(str.isspace), TEXT)
            if i < n and tag[i] in "/>?":
                add(tag[i:], MARKUP)
                return
            add(take(lambda c: not c.isspace() and c not in "=>"), ATTRIBUTE)
            if i < n and tag[i] == "=":
                add("=", MARKUP)
                i += 1
            if i < n and tag[i] == '"':
                start = i
                i += 1
                while i < n and tag[i] != '"':
                    i += 1
                if i < n:
                    i += 1
                add(tag[start:i], VALUE)

    i, n = 0, len(source)
    while i < n:
        if source[i] == "<":
            if source.startswith("<!--", i):
                end = source.find("-->", i + 4)
                end = n if end == -1 else end + 3
                add(source[i:end], COMMENT)
                i = end
                continue
            end = source.find(">", i)
            end = n if end == -1 else end + 1
            add_tag(source[i:end])
            i = end
        else:
            end = source.find("<", i)
            end = n if end == -1 else end
            add(source[i:end], TEXT)
            i = end
    return out


# --------------------------------------------------------------------------
# ツリー
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Entry:
    """ツリーの 1 行。常に全展開して見せるので、深さを持たせて一次元にする。"""

    path: Path
    name: str
    is_dir: bool
    depth: int


#: 中身ではなく環境が作るもの。見せても意味がない。
_IGNORED = {".DS_Store", "Thumbs.db", "desktop.ini"}


def walk(root: Path, *, depth: int = 0) -> list[Entry]:
    """フォルダを先、名前順で並べた一次元のリストを返す。"""
    entries = [Entry(root, root.name, True, depth)]
    try:
        children = sorted(
            (p for p in root.iterdir() if p.name not in _IGNORED),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except OSError:
        return entries
    for child in children:
        if child.is_dir():
            entries.extend(walk(child, depth=depth + 1))
        else:
            entries.append(Entry(child, child.name, False, depth + 1))
    return entries
