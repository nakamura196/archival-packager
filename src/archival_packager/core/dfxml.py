"""DFXML (Digital Forensics XML) 出力。

退役した Swift 実装の `Sources/SIP/DFXML.swift` に由来する。

本家 sipcreator は simsong/dfxml の walk_to_dfxml.py で生成する。当アプリは
既に保持している path/size/mtime/SHA-256 から、ディレクトリ走査相当の
サブセットを組み立てる。出力先は本家準拠で
metadata/submissionDocumentation/dfxml.xml。

METS と同じ理由で lxml を使う。Swift 版の esc() は `'` をエスケープしておらず、
属性値に `'` を含むパスで壊れる余地があった（属性区切りに `"` を使っているので
実害は出にくいが、自前エスケープの危うさそのものは残る）。

## 画像の技術的特性を、DFXML のどこに書くか

DFXML の語彙はファイルとしての事実（サイズ・時刻・ハッシュ）しか持たず、
画素数や色空間に当たる要素が無い。そこで **DFXML のスキーマが用意している拡張点**
に置く。`fileobject` の内容モデルは末尾が
`<xs:any namespace="##other" processContents="lax" maxOccurs="unbounded"/>` で、
別の名前空間の要素を最後に並べてよいと明記されている
（dfxml_schema 2.0.0-beta.0 の fileobject_type で確認）。既存の要素を勝手に
足したり並べ替えたりしないので、適合を壊さない。

**NISO MIX は使わなかった。** 画像技術メタデータの標準はあちらだが、(1) 手元に
XSD を置いて検証する用意が無く、「MIX で書いた」と言うだけになる。
(2) MIX の typeOfColorSpace は固定語彙で、Pillow の mode（"P" や "I;16"）を
そこへ押し込むには推測が要る。**検証できない適合の主張はしない**という
このリポジトリの方針に従い、自分の名前空間で、道具が言ったことを道具の語のまま書く。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import PIL
from lxml import etree

#: 版は 1 か所（archival_packager.__version__）で持つ。ここに直書きすると
#: 版を上げたときに追随せず、**来歴記録が誤った道具名を主張する**。
#: 実際に 0.1.2 を配ったあとも 0.1.0 と記録されていた。
from archival_packager import __version__ as PROGRAM_VERSION

from .models import ImageCharacteristics, ScannedFile

DC_NS = "http://purl.org/dc/elements/1.1/"

#: 当アプリ独自の要素を置く名前空間。DFXML の `##other` 拡張点に入れるので、
#: DFXML 自身の名前空間とは別でなければならない。
#: **一度出した URI は変えない。** 過去に作ったパッケージの dfxml.xml に
#: 書き込まれており、変えると後から読む側が同じものだと判定できなくなる。
#: 解決できる URL にしてあるのは、見つけた人が何者か調べられるようにするため。
#: 2026-09-24 にサイトを https://ap.ldas.jp へ移したが、この URI は変えない
#: （旧 URL へのアクセスは GitHub が新しいドメインへ転送する）。
AP_NS = "https://nakamura196.github.io/archival-packager/ns/dfxml/1.0"

PROGRAM_NAME = "Archival Packager"

#: 画像を読んだ道具。`<creator>` に 1 度だけ書く。
#: 値は fileobject ごとには書かない（数万件ぶん同じ文字列が並ぶだけになる）。
IMAGE_TOOL_NAME = "Pillow"


def build(
    files: list[ScannedFile],
    input_root: Path,
    *,
    start_time: datetime | None = None,
    full_source_path: bool = False,
) -> bytes:
    """DFXML を組み立てて UTF-8 のバイト列で返す。"""
    started = start_time or datetime.now(UTC)

    # 画像を 1 件も見ていない移管では、名前空間の宣言も <library> も出さない。
    # 使っていない道具を来歴に書くと、読んだ人は「Pillow で何かした」と読む。
    characterised = any(f.image is not None for f in files)

    root = etree.Element(
        "dfxml", xmloutputversion="1.0", nsmap={"ap": AP_NS} if characterised else None
    )

    metadata = etree.SubElement(root, "metadata", nsmap={"dc": DC_NS})
    etree.SubElement(metadata, f"{{{DC_NS}}}type").text = "File system walk"

    creator = etree.SubElement(root, "creator", version="1.0")
    etree.SubElement(creator, "program").text = PROGRAM_NAME
    etree.SubElement(creator, "version").text = PROGRAM_VERSION
    env = etree.SubElement(creator, "execution_environment")
    etree.SubElement(env, "start_time").text = _iso(started)

    # DFXML の <library> は creator の下に置くと「実行時に使った版」を意味する
    # （build_environment の下なら「ビルド時の版」）。画像から読んだ値は
    # Pillow の版によって変わりうるので、どの版が言ったことなのかを残す。
    # 要素の順序は creator の内容モデル（program → version →
    # build_environment → execution_environment → library）に合わせてある。
    if characterised:
        etree.SubElement(creator, "library", name=IMAGE_TOOL_NAME, version=PIL.__version__)

    source = etree.SubElement(root, "source")
    # **既定ではフォルダ名だけを残す。** 絶対パスには利用者名が入る
    #   （例: C:\Users\<名前>\Desktop\移管 2026）。
    # AIP は外部に渡りうるもので、このアプリ自身は個人情報を検出する機能を
    # 持っている。自分が利用者名を埋め込むのは筋が通らない。
    # 組織の方針として完全なパスを残したい場合は full_source_path で切り替える。
    etree.SubElement(source, "image_filename").text = (
        str(input_root) if full_source_path else input_root.name
    )

    for i, f in enumerate(files, start=1):
        obj = etree.SubElement(root, "fileobject")
        etree.SubElement(obj, "filename").text = f.relative_path
        etree.SubElement(obj, "id").text = str(i)
        etree.SubElement(obj, "name_type").text = "r"
        etree.SubElement(obj, "filesize").text = str(f.size_bytes)
        etree.SubElement(obj, "mtime").text = _iso(f.modified)
        if f.sha256:
            etree.SubElement(obj, "hashdigest", type="sha256").text = f.sha256
        if f.image is not None:
            # **必ず最後に置く。** DFXML が別名前空間の要素を許しているのは
            # fileobject の内容モデルの末尾だけ（冒頭の説明を参照）。
            _append_image(obj, f.image)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _append_image(obj: etree._Element, image: ImageCharacteristics) -> None:
    """画像の技術的特性を fileobject に足す。

    readable 属性は**読めたときも必ず書く**。属性が無いことを「読めた」と
    解釈させると、こちらが値を書き忘れた場合と見分けが付かない。
    """
    el = etree.SubElement(obj, f"{{{AP_NS}}}image", readable=_bool(image.readable))
    if not image.readable:
        etree.SubElement(el, f"{{{AP_NS}}}error").text = image.error
        return

    # 取れた項目だけを書く。持っていなかった値を推測で埋めない
    # （DPI を持たない画像に 72 を補うと、事実と区別が付かなくなる）。
    for name, value in (
        ("width", image.width),
        ("height", image.height),
        ("color_space", image.color_space),
        ("bits_per_sample", image.bits_per_sample),
        ("x_dpi", _number(image.x_dpi)),
        ("y_dpi", _number(image.y_dpi)),
    ):
        if value is not None:
            etree.SubElement(el, f"{{{AP_NS}}}{name}").text = str(value)


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _number(value: float | None) -> str | None:
    """dpi の表記。300.0 を "300" と書く（"300.0" は原本に無い精度を主張する）。"""
    if value is None:
        return None
    return format(value, "g")


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
