"""SIP を作ったときの記録を METS に包んで書き出す・AIP に置く（Issue #49）。

なぜ要るか
----------
Archivematica の AIP は、受け入れ時（transfer）の METS を
``objects/submissionDocumentation/transfer-<名前>-<uuid>/METS.xml`` に残す
（aip-structure 1.16）。ウイルス検査などの受け入れ段の記録もそこにある。
こちらは 2026-10-27 まで PREMIS だけの premis.xml しか書いておらず、
**中身は同じでも、Archivematica の AIP と同じ形を前提に読む道具からは見つからなかった**
（docs/interoperability.md の Gap 12）。

決めたこと
----------
- **SIP の中の名前は METS.xml にしない。** Archivematica はこの SIP を取り込むと、
  提出書類を transfer-<名前>-<uuid>/ に移し、そこへ自分の METS.xml を書く。
  同じ名前だと、どちらかが消える。SIP の中では ``sip-mets.xml`` とし、
  このアプリが AIP を作るときにだけ ``transfer-<SIP の名前>-<uuid>/METS.xml`` に置く。
- **premis.xml は残す。** 中身は同じ記録で、collect() で 1 度だけ作ったものを
  2 つの形で書く（event の識別子も一致する）。AIP 作成と確認画面は
  premis.xml を読む。2026-10-27 から 10-28 の間に作った SIP には premis.xml しか無く、
  読み手を METS に切り替えると、その SIP から作る AIP の記録の日時が変わる。
- **SIP にはまだ UUID が無い**（原本の UUID は AIP を作るときに振る）。
  そこで SIP 自体の UUID をここで 1 つ振り、METS の OBJID に書く。
  object の識別子は premis.xml と同じく objects/ からの相対パス。
- fileSec・structMap の ID は通し番号。相対パスは XML の ID に使えない。
- 提出書類の他のファイル（formats.csv など）は、AIP でも今の位置に置いたまま。
  Archivematica はこれらも transfer-*/ の下に入れるが、移すとこのアプリの確認画面と
  既存の AIP の読み手が辿れなくなる。突合表に残す差分として書いてある。
"""

from __future__ import annotations

import uuid as _uuid

from lxml import etree

from . import mets, sip_premis
from .aip_models import PremisAgent, PremisEvent
from .models import ScannedFile

METS_NS = "http://www.loc.gov/METS/"
XLINK_NS = "http://www.w3.org/1999/xlink"

#: SIP の submissionDocumentation の中での名前。
FILE_NAME = "sip-mets.xml"

#: AIP の中での名前（Archivematica の transfer METS と同じ）。
AIP_FILE_NAME = "METS.xml"

_SAFE_PARSER = etree.XMLParser(
    resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True
)


def new_identifier() -> str:
    return str(_uuid.uuid4())


def transfer_dir_name(sip_name: str, sip_uuid: str) -> str:
    """AIP の submissionDocumentation の下に作るフォルダ名。"""
    return f"transfer-{sip_name}-{sip_uuid}"


def _m(tag: str) -> str:
    return f"{{{METS_NS}}}{tag}"


def build(
    per_file: list[tuple[ScannedFile, list[PremisEvent]]],
    agents: list[PremisAgent],
    *,
    sip_uuid: str,
    created: str,
    app_name: str,
) -> bytes:
    """sip-mets.xml のバイト列。per_file と agents は sip_premis.collect() の結果。"""
    nsmap = {
        "mets": METS_NS,
        "premis": sip_premis.PREMIS_NS,
        "xlink": XLINK_NS,
        "xsi": sip_premis.XSI_NS,
    }
    root = etree.Element(_m("mets"), nsmap=nsmap, OBJID=sip_uuid, TYPE="SIP")

    hdr = etree.SubElement(root, _m("metsHdr"), CREATEDATE=created, RECORDSTATUS="complete")
    agent = etree.SubElement(
        hdr, _m("agent"), ROLE="CREATOR", TYPE="OTHER", OTHERTYPE="SOFTWARE"
    )
    etree.SubElement(agent, _m("name")).text = app_name

    by_id = {a.id: a for a in agents}

    # 1 ファイルに 1 つの amdSec。Archivematica の transfer METS と同じく、
    # その event が参照する agent も同じ amdSec に入れる（amdSec だけで読み切れる）。
    for n, (f, events) in enumerate(per_file, start=1):
        amd = etree.SubElement(root, _m("amdSec"), ID=f"amdSec_{n}")
        tech = etree.SubElement(amd, _m("techMD"), ID=f"techMD_{n}")
        sip_premis.append_object(_wrap(tech, "PREMIS:OBJECT"), f)

        k = 0
        linked: list[str] = []
        for ev in events:
            k += 1
            digiprov = etree.SubElement(amd, _m("digiprovMD"), ID=f"digiprovMD_{n}_{k}")
            sip_premis.append_event(_wrap(digiprov, "PREMIS:EVENT"), ev, f.relative_path)
            linked += [a for a in ev.agent_ids if a not in linked]
        for aid in linked:
            k += 1
            digiprov = etree.SubElement(amd, _m("digiprovMD"), ID=f"digiprovMD_{n}_{k}")
            sip_premis.append_agent(_wrap(digiprov, "PREMIS:AGENT"), by_id[aid])

    file_sec = etree.SubElement(root, _m("fileSec"))
    grp = etree.SubElement(file_sec, _m("fileGrp"), USE="original")
    for n, (f, _events) in enumerate(per_file, start=1):
        attrs = {"ID": f"file-{n}", "ADMID": f"amdSec_{n}"}
        if f.sha256:
            attrs.update(CHECKSUM=f.sha256, CHECKSUMTYPE="SHA-256")
        el = etree.SubElement(grp, _m("file"), **attrs)
        loc = etree.SubElement(el, _m("FLocat"), LOCTYPE="OTHER", OTHERLOCTYPE="SYSTEM")
        loc.set(f"{{{XLINK_NS}}}href", f"objects/{f.relative_path}")

    _append_struct_map(root, sip_uuid, [(f.relative_path, f"file-{n}")
                                        for n, (f, _e) in enumerate(per_file, start=1)])

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _wrap(parent: etree._Element, mdtype: str) -> etree._Element:
    wrap = etree.SubElement(parent, _m("mdWrap"), MDTYPE=mdtype)
    return etree.SubElement(wrap, _m("xmlData"))


def _append_struct_map(
    parent: etree._Element, label: str, entries: list[tuple[str, str]]
) -> None:
    """相対パスから入れ子の div を組む。並びは名前順（同じ入力から同じ METS）。

    AIP の METS（core/mets.py）と同じ並び: 外側の div の中に "objects" の div。
    外側の LABEL は SIP の UUID（SIP のフォルダ名はこの時点ではまだ決まっていない）。
    """
    tree: dict = {}
    for rel, file_id in entries:
        *dirs, name = rel.split("/")
        node = tree
        for d in dirs:
            node = node.setdefault(d, {})
        node[name] = file_id

    struct_map = etree.SubElement(
        parent, _m("structMap"), TYPE="physical", LABEL=mets.STRUCT_MAP_LABEL
    )
    outer = etree.SubElement(struct_map, _m("div"), TYPE=mets.DIV_DIRECTORY, LABEL=label)
    top = etree.SubElement(outer, _m("div"), TYPE=mets.DIV_DIRECTORY, LABEL="objects")
    _render(top, tree)


def _render(parent: etree._Element, node: dict) -> None:
    for name in sorted(k for k, v in node.items() if isinstance(v, dict)):
        div = etree.SubElement(parent, _m("div"), TYPE=mets.DIV_DIRECTORY, LABEL=name)
        _render(div, node[name])
    for name in sorted(k for k, v in node.items() if isinstance(v, str)):
        div = etree.SubElement(parent, _m("div"), TYPE=mets.DIV_ITEM, LABEL=name)
        etree.SubElement(div, _m("fptr"), FILEID=node[name])


def read_identifier(data: bytes) -> str:
    """sip-mets.xml の OBJID（SIP の UUID）。読めなければ例外。"""
    doc = etree.fromstring(data, _SAFE_PARSER)
    if doc.tag != _m("mets"):
        raise ValueError("METS ではありません")
    # フォルダ名に入れるので、UUID の形でなければ受け付けない
    # （"../" などを含む値で AIP の外に書かせない）。
    return str(_uuid.UUID((doc.get("OBJID") or "").strip()))
