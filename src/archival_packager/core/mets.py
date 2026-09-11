"""METS / PREMIS の生成。

現行 Swift 実装の `Sources/AIP/METSWriter.swift` と `PREMISWriter.swift` に対応する。
amdSec（PREMIS object/event/agent を内包）/ fileSec / structMap を 1 つの METS にまとめる。
admID・fileID を 3 セクション間で一貫させるのが要点。

## 文字列連結をやめて lxml で組む

Swift 版は XML を文字列連結で作っており、次の弱点があった。

1. **エスケープが自前。** `esc()` の適用漏れが 1 箇所でもあると、資料名に & や <
   を含むだけで壊れた XML を出す。壊れていても生成は成功するため気づけない。
2. **構造の誤りを検出できない。** 閉じ忘れや入れ子の誤りがそのまま出力される。
3. **検証できない。** METS/PREMIS のスキーマに適合しているか確かめる手段が無い。

lxml で組めば 1 と 2 は原理的に起こらない（テキストは常にエスケープされ、
木構造なので閉じ忘れも起こらない）。3 も **公式の XSD で検証している**
（tests/test_schema_conformance.py。スキーマは tests/schemas/ に置いてある）。
これが Python を移植先に選んだ理由の一つ。
"""

from __future__ import annotations

from dataclasses import dataclass

from lxml import etree

from .aip_models import AgentKind, AIPFile, DescriptiveMetadata, PremisAgent, PremisEvent

METS_NS = "http://www.loc.gov/METS/"
PREMIS_NS = "http://www.loc.gov/premis/v3"
XLINK_NS = "http://www.w3.org/1999/xlink"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"

NSMAP = {
    "mets": METS_NS,
    "premis": PREMIS_NS,
    "xlink": XLINK_NS,
    "xsi": XSI_NS,
}


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def agent_id_type(kind: AgentKind) -> str:
    """PREMIS の agentIdentifierType を agent 種別から決める。

    software = preservation-system（Archivematica 準拠）。
    """
    return {
        AgentKind.SOFTWARE: "preservation-system",
        AgentKind.HUMAN: "archivist",
        AgentKind.ORGANIZATION: "organization",
    }[kind]


@dataclass(frozen=True)
class SubmissionDocument:
    """提出書類の 1 件。METS の fileSec に載せるために使う。

    PREMIS の object は持たない（原本でも派生物でもなく、処理の記録そのもの）。
    """

    href: str   # AIP の data/ からの相対パス
    uuid: str


def build_mets(
    *,
    aip_uuid: str,
    files: list[AIPFile],
    agents: list[PremisAgent],
    descriptive: DescriptiveMetadata | None,
    created_iso: str,
    submission_documentation: list[SubmissionDocument] | None = None,
) -> bytes:
    """METS を組み立てて UTF-8 のバイト列で返す。"""
    root = etree.Element(_q(METS_NS, "mets"), nsmap=NSMAP)

    etree.SubElement(
        root, _q(METS_NS, "metsHdr"), CREATEDATE=created_iso, RECORDSTATUS="complete"
    )

    # dmdSec。SIP 全体と file 単位の両方。
    whole_dmd_id = "dmdSec_whole"
    has_whole_dmd = descriptive is not None and descriptive.has_any
    if has_whole_dmd:
        _append_dmd_sec(root, descriptive, whole_dmd_id)

    file_dmd_ids: dict[str, str] = {}
    for n, f in enumerate(files):
        if f.descriptive is not None and f.descriptive.has_any:
            dmd_id = f"dmdSec_obj{n}"
            _append_dmd_sec(root, f.descriptive, dmd_id)
            file_dmd_ids[f.relative_path] = dmd_id

    # amdSec: まず agent をまとめて 1 つ、続いて各オブジェクト。
    agent_kinds = {a.id: a.kind for a in agents}
    _append_agents_amd_sec(root, agents)

    # 各原本・派生物に admID/fileID を割り当て、3 セクションで共有する。
    entries: list[dict[str, object]] = []
    for n, f in enumerate(files):
        adm_id = f"amdSec_{n}"
        _append_object_amd_sec(
            root,
            adm_id=adm_id,
            object_uuid=f.uuid,
            sha256=f.sha256,
            size=f.size_bytes,
            format_name=f.format_name,
            puid=f.puid,
            original_name=f.relative_path,
            related_uuid=None,
            relationship_subtype=None,
            events=f.events,
            agent_kinds=agent_kinds,
        )
        entries.append(
            {
                "original": True,
                "file_id": f"file-{f.uuid}",
                "adm_id": adm_id,
                "href": f"objects/{f.relative_path}",
                "relative_path": f.relative_path,
            }
        )

        for m, d in enumerate(f.derivatives):
            d_adm_id = f"amdSec_{n}_d{m}"
            _append_object_amd_sec(
                root,
                adm_id=d_adm_id,
                object_uuid=d.uuid,
                sha256=d.sha256,
                size=d.size_bytes,
                format_name=None,
                puid=d.puid_out,
                original_name=None,
                related_uuid=f.uuid,
                relationship_subtype="is normalized version of",
                events=[],
                agent_kinds=agent_kinds,
            )
            entries.append(
                {
                    "original": False,
                    "file_id": f"file-{d.uuid}",
                    "adm_id": d_adm_id,
                    "href": f"objects/{d.relative_path}",
                    "relative_path": d.relative_path,
                }
            )

    # fileSec
    file_sec = etree.SubElement(root, _q(METS_NS, "fileSec"))
    _append_file_grp(file_sec, "original", [e for e in entries if e["original"]])
    preservation = [e for e in entries if not e["original"]]
    if preservation:
        _append_file_grp(file_sec, "preservation", preservation)

    # **提出書類も METS に載せる。** AIP に入れているのに fileSec に無いと、
    # METS だけを読む側からは存在しないことになる。Archivematica も
    # submissionDocumentation の fileGrp を持つ。
    if submission_documentation:
        _append_file_grp(
            file_sec,
            "submissionDocumentation",
            [
                {
                    "original": False,
                    "file_id": f"file-{d.uuid}",
                    "adm_id": None,
                    "href": d.href,
                    "relative_path": d.href,
                }
                for d in submission_documentation
            ],
        )

    # structMap（原本の物理ツリー）
    _append_struct_map(
        root, files, whole_dmd_id if has_whole_dmd else None, file_dmd_ids
    )

    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )


# --------------------------------------------------------------------------
# dmdSec（Dublin Core）
# --------------------------------------------------------------------------


def _append_dmd_sec(parent: etree._Element, d: DescriptiveMetadata, dmd_id: str) -> None:
    dmd = etree.SubElement(parent, _q(METS_NS, "dmdSec"), ID=dmd_id)
    wrap = etree.SubElement(dmd, _q(METS_NS, "mdWrap"), MDTYPE="DC")
    xml_data = etree.SubElement(wrap, _q(METS_NS, "xmlData"))
    dublincore = etree.SubElement(
        xml_data,
        _q(DCTERMS_NS, "dublincore"),
        nsmap={"dc": DC_NS, "dcterms": DCTERMS_NS},
    )

    for ns, tag, value in (
        (DC_NS, "title", d.title),
        (DC_NS, "identifier", d.identifier),
        (DC_NS, "creator", d.creator),
        (DC_NS, "date", d.date),
        (DC_NS, "description", d.description),
        (DCTERMS_NS, "extent", d.extent),
        (DC_NS, "language", d.language),
        (DCTERMS_NS, "accessRights", d.access_rights),
    ):
        if value:
            etree.SubElement(dublincore, _q(ns, tag)).text = value


# --------------------------------------------------------------------------
# amdSec / PREMIS
# --------------------------------------------------------------------------


def _append_agents_amd_sec(parent: etree._Element, agents: list[PremisAgent]) -> None:
    """全 agent をまとめた 1 つの amdSec。各 event はここを linking で参照する。"""
    amd = etree.SubElement(parent, _q(METS_NS, "amdSec"), ID="amdSec_agents")
    for i, a in enumerate(agents):
        digiprov = etree.SubElement(
            amd, _q(METS_NS, "digiprovMD"), ID=f"digiprovMD_agent_{i}"
        )
        xml_data = _wrap(digiprov, "PREMIS:AGENT")
        _append_agent(xml_data, a)


def _append_object_amd_sec(
    parent: etree._Element,
    *,
    adm_id: str,
    object_uuid: str,
    sha256: str | None,
    size: int,
    format_name: str | None,
    puid: str | None,
    original_name: str | None,
    related_uuid: str | None,
    relationship_subtype: str | None,
    events: list[PremisEvent],
    agent_kinds: dict[str, AgentKind],
) -> None:
    """1 オブジェクト（原本 or 派生物）の amdSec。"""
    amd = etree.SubElement(parent, _q(METS_NS, "amdSec"), ID=adm_id)

    tech = etree.SubElement(amd, _q(METS_NS, "techMD"), ID=f"{adm_id}_techMD")
    _append_object(
        _wrap(tech, "PREMIS:OBJECT"),
        uuid=object_uuid,
        sha256=sha256,
        size=size,
        format_name=format_name,
        puid=puid,
        original_name=original_name,
        related_uuid=related_uuid,
        relationship_subtype=relationship_subtype,
    )

    for k, ev in enumerate(events):
        digiprov = etree.SubElement(
            amd, _q(METS_NS, "digiprovMD"), ID=f"{adm_id}_event_{k}"
        )
        _append_event(_wrap(digiprov, "PREMIS:EVENT"), ev, agent_kinds)


def _wrap(parent: etree._Element, mdtype: str) -> etree._Element:
    """mets:mdWrap/mets:xmlData を作り、中身を入れる要素を返す。"""
    wrap = etree.SubElement(parent, _q(METS_NS, "mdWrap"), MDTYPE=mdtype)
    return etree.SubElement(wrap, _q(METS_NS, "xmlData"))


def _append_object(
    parent: etree._Element,
    *,
    uuid: str,
    sha256: str | None,
    size: int,
    format_name: str | None,
    puid: str | None,
    original_name: str | None,
    related_uuid: str | None,
    relationship_subtype: str | None,
) -> None:
    obj = etree.SubElement(parent, _q(PREMIS_NS, "object"))
    obj.set(_q(XSI_NS, "type"), "premis:file")

    ident = etree.SubElement(obj, _q(PREMIS_NS, "objectIdentifier"))
    etree.SubElement(ident, _q(PREMIS_NS, "objectIdentifierType")).text = "UUID"
    etree.SubElement(ident, _q(PREMIS_NS, "objectIdentifierValue")).text = uuid

    chars = etree.SubElement(obj, _q(PREMIS_NS, "objectCharacteristics"))
    etree.SubElement(chars, _q(PREMIS_NS, "compositionLevel")).text = "0"
    if sha256:
        fixity = etree.SubElement(chars, _q(PREMIS_NS, "fixity"))
        etree.SubElement(fixity, _q(PREMIS_NS, "messageDigestAlgorithm")).text = "SHA-256"
        etree.SubElement(fixity, _q(PREMIS_NS, "messageDigest")).text = sha256
    etree.SubElement(chars, _q(PREMIS_NS, "size")).text = str(size)

    fmt = etree.SubElement(chars, _q(PREMIS_NS, "format"))
    designation = etree.SubElement(fmt, _q(PREMIS_NS, "formatDesignation"))
    etree.SubElement(designation, _q(PREMIS_NS, "formatName")).text = format_name or "unknown"
    if puid:
        registry = etree.SubElement(fmt, _q(PREMIS_NS, "formatRegistry"))
        etree.SubElement(registry, _q(PREMIS_NS, "formatRegistryName")).text = "PRONOM"
        etree.SubElement(registry, _q(PREMIS_NS, "formatRegistryKey")).text = puid

    if original_name:
        etree.SubElement(obj, _q(PREMIS_NS, "originalName")).text = original_name

    if related_uuid and relationship_subtype:
        rel = etree.SubElement(obj, _q(PREMIS_NS, "relationship"))
        etree.SubElement(rel, _q(PREMIS_NS, "relationshipType")).text = "derivation"
        etree.SubElement(rel, _q(PREMIS_NS, "relationshipSubType")).text = relationship_subtype
        related = etree.SubElement(rel, _q(PREMIS_NS, "relatedObjectIdentifier"))
        etree.SubElement(related, _q(PREMIS_NS, "relatedObjectIdentifierType")).text = "UUID"
        etree.SubElement(related, _q(PREMIS_NS, "relatedObjectIdentifierValue")).text = related_uuid


def _append_event(
    parent: etree._Element, ev: PremisEvent, agent_kinds: dict[str, AgentKind]
) -> None:
    event = etree.SubElement(parent, _q(PREMIS_NS, "event"))

    ident = etree.SubElement(event, _q(PREMIS_NS, "eventIdentifier"))
    etree.SubElement(ident, _q(PREMIS_NS, "eventIdentifierType")).text = "UUID"
    # Swift 版はここで UUID を都度生成していたため、同じ入力から 2 回生成すると
    # 別の値になり差分検証ができなかった。モデルが持つ識別子を使う。
    etree.SubElement(ident, _q(PREMIS_NS, "eventIdentifierValue")).text = ev.identifier

    etree.SubElement(event, _q(PREMIS_NS, "eventType")).text = ev.type
    etree.SubElement(event, _q(PREMIS_NS, "eventDateTime")).text = ev.date_time

    outcome_info = etree.SubElement(event, _q(PREMIS_NS, "eventOutcomeInformation"))
    etree.SubElement(outcome_info, _q(PREMIS_NS, "eventOutcome")).text = ev.outcome
    if ev.detail_note:
        detail = etree.SubElement(outcome_info, _q(PREMIS_NS, "eventOutcomeDetail"))
        etree.SubElement(detail, _q(PREMIS_NS, "eventOutcomeDetailNote")).text = ev.detail_note

    for aid in ev.agent_ids:
        kind = agent_kinds.get(aid)
        id_type = agent_id_type(kind) if kind else "preservation-system"
        linking = etree.SubElement(event, _q(PREMIS_NS, "linkingAgentIdentifier"))
        etree.SubElement(linking, _q(PREMIS_NS, "linkingAgentIdentifierType")).text = id_type
        etree.SubElement(linking, _q(PREMIS_NS, "linkingAgentIdentifierValue")).text = aid


def _append_agent(parent: etree._Element, a: PremisAgent) -> None:
    agent = etree.SubElement(parent, _q(PREMIS_NS, "agent"))
    ident = etree.SubElement(agent, _q(PREMIS_NS, "agentIdentifier"))
    etree.SubElement(ident, _q(PREMIS_NS, "agentIdentifierType")).text = agent_id_type(a.kind)
    etree.SubElement(ident, _q(PREMIS_NS, "agentIdentifierValue")).text = a.id
    etree.SubElement(agent, _q(PREMIS_NS, "agentName")).text = a.name
    etree.SubElement(agent, _q(PREMIS_NS, "agentType")).text = a.kind.value


# --------------------------------------------------------------------------
# fileSec / structMap
# --------------------------------------------------------------------------


def _append_file_grp(parent: etree._Element, use: str, entries: list[dict]) -> None:
    """fileGrp を 1 つ足す。adm_id が None の項目には ADMID を付けない
    （提出書類は PREMIS の object を持たない）。"""
    grp = etree.SubElement(parent, _q(METS_NS, "fileGrp"), USE=use)
    for e in entries:
        attrs = {"ID": str(e["file_id"])}
        if e.get("adm_id"):
            attrs["ADMID"] = str(e["adm_id"])
        f = etree.SubElement(grp, _q(METS_NS, "file"), **attrs)
        flocat = etree.SubElement(
            f, _q(METS_NS, "FLocat"), LOCTYPE="OTHER", OTHERLOCTYPE="SYSTEM"
        )
        flocat.set(_q(XLINK_NS, "href"), str(e["href"]))


class _Node:
    __slots__ = ("dirs", "files")

    def __init__(self) -> None:
        self.dirs: dict[str, _Node] = {}
        self.files: list[tuple[str, str, str | None]] = []  # (name, file_id, dmd_id)


def _append_struct_map(
    parent: etree._Element,
    files: list[AIPFile],
    dmd_id: str | None,
    file_dmd_ids: dict[str, str],
) -> None:
    """相対パスから入れ子 div を組む。"""
    root_node = _Node()
    for f in files:
        comps = f.relative_path.split("/")
        name = comps.pop()
        cur = root_node
        for d in comps:
            cur = cur.dirs.setdefault(d, _Node())
        cur.files.append((name, f"file-{f.uuid}", file_dmd_ids.get(f.relative_path)))

    struct_map = etree.SubElement(parent, _q(METS_NS, "structMap"), TYPE="physical")
    attrs = {"TYPE": "directory", "LABEL": "objects"}
    if dmd_id:
        attrs["DMDID"] = dmd_id
    top = etree.SubElement(struct_map, _q(METS_NS, "div"), **attrs)
    _render_node(top, root_node)


def _render_node(parent: etree._Element, node: _Node) -> None:
    # 並び順を固定する。dict の挿入順に任せると入力の走査順で METS が変わり、
    # 同じ資料から 2 回作った AIP を突合できなくなる。
    for name in sorted(node.dirs):
        div = etree.SubElement(
            parent, _q(METS_NS, "div"), TYPE="directory", LABEL=name
        )
        _render_node(div, node.dirs[name])

    for name, file_id, dmd_id in sorted(node.files, key=lambda t: t[0]):
        attrs = {"TYPE": "item", "LABEL": name}
        if dmd_id:
            attrs["DMDID"] = dmd_id
        div = etree.SubElement(parent, _q(METS_NS, "div"), **attrs)
        etree.SubElement(div, _q(METS_NS, "fptr"), FILEID=file_id)
