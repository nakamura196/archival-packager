"""DFXML (Digital Forensics XML) 出力。

現行 Swift 実装の `Sources/SIP/DFXML.swift` に対応する。

本家 sipcreator は simsong/dfxml の walk_to_dfxml.py で生成する。当アプリは
既に保持している path/size/mtime/SHA-256 から、ディレクトリ走査相当の
サブセットを組み立てる。出力先は本家準拠で
metadata/submissionDocumentation/dfxml.xml。

METS と同じ理由で lxml を使う。Swift 版の esc() は `'` をエスケープしておらず、
属性値に `'` を含むパスで壊れる余地があった（属性区切りに `"` を使っているので
実害は出にくいが、自前エスケープの危うさそのものは残る）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from .models import ScannedFile

DC_NS = "http://purl.org/dc/elements/1.1/"

PROGRAM_NAME = "Archival Packager"
#: 版は 1 か所（archival_packager.__version__）で持つ。ここに直書きすると
#: 版を上げたときに追随せず、**来歴記録が誤った道具名を主張する**。
#: 実際に 0.1.2 を配ったあとも 0.1.0 と記録されていた。
from archival_packager import __version__ as PROGRAM_VERSION


def build(
    files: list[ScannedFile],
    input_root: Path,
    *,
    start_time: datetime | None = None,
    full_source_path: bool = False,
) -> bytes:
    """DFXML を組み立てて UTF-8 のバイト列で返す。"""
    started = start_time or datetime.now(timezone.utc)

    root = etree.Element("dfxml", xmloutputversion="1.0")

    metadata = etree.SubElement(root, "metadata", nsmap={"dc": DC_NS})
    etree.SubElement(metadata, f"{{{DC_NS}}}type").text = "File system walk"

    creator = etree.SubElement(root, "creator", version="1.0")
    etree.SubElement(creator, "program").text = PROGRAM_NAME
    etree.SubElement(creator, "version").text = PROGRAM_VERSION
    env = etree.SubElement(creator, "execution_environment")
    etree.SubElement(env, "start_time").text = _iso(started)

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

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
