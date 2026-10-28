"""SIP を作るときに行った処理を、PREMIS の記録として書き出す・読み戻す。

なぜ要るか
----------
SIP を作るときに、ファイル名の安全化・フォーマット識別・チェックサムの算出・
ウイルス検査を行っている。2026-10-27 まで、その結果は formats.csv の列と
report.txt の文章にしか残らず、**いつ・どの道具で行ったかの記録が無かった。**
AIP 側はその列を読んで event を作り直していたが、日時は AIP を作った時刻に
なっていた。SIP を作ってから AIP にするまで日が空けば、記録の日時は事実と違う。

そこで、処理を行ったその時に event を作り、submissionDocumentation/premis.xml
に書く。AIP はこれを読み、日時と識別子をそのまま METS に移す。

書き方の決まり
--------------
- **行わなかった処理の event は書かない。** aip_pipeline と同じ決まり。
  書かないことが「不明」を表し、「検査して検出なし」と取り違えない。
- object の識別子は objects/ からの相対パス。SIP の段ではまだ UUID が無い
  （UUID は AIP を作るときに振る）。
- 置き場所は metadata/ 直下ではなく submissionDocumentation/ の下。
  Archivematica は submissionDocumentation の中身をそのまま保存するので、
  名前がほかの取り込み規約と衝突しない。
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lxml import etree

from .aip_models import AgentKind, PremisAgent, PremisEvent
from .models import ScannedFile

PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

#: submissionDocumentation の中のファイル名。
FILE_NAME = "premis.xml"

#: object の識別子の種類。UUID ではないことを明示する。
OBJECT_ID_TYPE = "SIP relative path"

#: 道具の版が取れなかったときの agent。aip_pipeline の linkingAgent と同じ値。
#: 版が取れたときは siegfried.release() / clamav.release() の値を使う。
IDENTIFICATION_AGENT = "Siegfried (PRONOM)"
VIRUS_AGENT = "ClamAV"

_SAFE_PARSER = etree.XMLParser(
    resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True
)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(slots=True)
class Stage:
    """SIP 作成の 1 段。始めた時刻と、行った道具。"""

    event_type: str
    date_time: str
    agent_id: str


@dataclass(slots=True)
class SIPProvenance:
    """SIP 作成中に行った段の記録。行わなかった段は入れない。"""

    app_agent: PremisAgent
    stages: list[Stage] = field(default_factory=list)

    def record(self, event_type: str, agent_id: str | None = None) -> None:
        self.stages.append(Stage(event_type, now_iso(), agent_id or self.app_agent.id))


# --------------------------------------------------------------------------
# 書き出し
# --------------------------------------------------------------------------


def _q(tag: str) -> str:
    return f"{{{PREMIS_NS}}}{tag}"


def events_for(f: ScannedFile, stage: Stage, app_agent_id: str) -> PremisEvent | None:
    """1 ファイル・1 段の event。その段がこのファイルに何もしていなければ None。"""
    agents = [app_agent_id] if stage.agent_id == app_agent_id else [app_agent_id, stage.agent_id]
    kind = stage.event_type

    if kind == "filename change":
        if not f.original_relative_path or f.original_relative_path == f.relative_path:
            return None
        outcome, note = "success", f"{f.original_relative_path} → {f.relative_path}"
    elif kind == "format identification":
        if f.puid:
            outcome = "success"
            note = f"PRONOM {f.puid}" + (f"（{f.format_name}）" if f.format_name else "")
        else:
            outcome, note = "fail", "識別できませんでした"
    elif kind == "message digest calculation":
        if not f.sha256:
            return None
        outcome, note = "success", f"SHA-256 {f.sha256}"
    elif kind == "virus check":
        if not f.scanned_for_virus:
            return None
        if f.virus:
            outcome, note = "fail", f"検出: {f.virus}"
        else:
            outcome, note = "pass", "検出なし"
    else:
        return None

    return PremisEvent(
        type=kind, date_time=stage.date_time, detail_note=note,
        outcome=outcome, agent_ids=agents,
    )


def build(files: list[ScannedFile], provenance: SIPProvenance) -> bytes:
    """premis.xml のバイト列。object・event・agent をこの順に並べる（PREMIS 3 の順序）。"""
    root = etree.Element(_q("premis"), nsmap={"premis": PREMIS_NS, "xsi": XSI_NS})
    root.set("version", "3.0")
    app_id = provenance.app_agent.id

    for f in files:
        _append_object(root, f)

    used_agents: list[str] = []
    for f in files:
        for stage in provenance.stages:
            ev = events_for(f, stage, app_id)
            if ev is None:
                continue
            _append_event(root, ev, f.relative_path)
            for a in ev.agent_ids:
                if a not in used_agents:
                    used_agents.append(a)

    known = {provenance.app_agent.id: provenance.app_agent}
    for a in used_agents:
        agent = known.get(a) or PremisAgent(a, a, AgentKind.SOFTWARE)
        _append_agent(root, agent)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _append_object(parent: etree._Element, f: ScannedFile) -> None:
    obj = etree.SubElement(parent, _q("object"))
    obj.set(f"{{{XSI_NS}}}type", "premis:file")
    _append_identifier(obj, "objectIdentifier", f.relative_path)

    chars = etree.SubElement(obj, _q("objectCharacteristics"))
    etree.SubElement(chars, _q("compositionLevel")).text = "0"
    if f.sha256:
        fixity = etree.SubElement(chars, _q("fixity"))
        etree.SubElement(fixity, _q("messageDigestAlgorithm")).text = "SHA-256"
        etree.SubElement(fixity, _q("messageDigest")).text = f.sha256
    etree.SubElement(chars, _q("size")).text = str(f.size_bytes)
    fmt = etree.SubElement(chars, _q("format"))
    designation = etree.SubElement(fmt, _q("formatDesignation"))
    etree.SubElement(designation, _q("formatName")).text = f.format_name or "unknown"
    if f.puid:
        registry = etree.SubElement(fmt, _q("formatRegistry"))
        etree.SubElement(registry, _q("formatRegistryName")).text = "PRONOM"
        etree.SubElement(registry, _q("formatRegistryKey")).text = f.puid

    if f.original_relative_path:
        etree.SubElement(obj, _q("originalName")).text = f.original_relative_path


def _append_identifier(parent: etree._Element, tag: str, value: str) -> None:
    ident = etree.SubElement(parent, _q(tag))
    etree.SubElement(ident, _q(f"{tag}Type")).text = OBJECT_ID_TYPE
    etree.SubElement(ident, _q(f"{tag}Value")).text = value


def _append_event(parent: etree._Element, ev: PremisEvent, target: str) -> None:
    event = etree.SubElement(parent, _q("event"))
    ident = etree.SubElement(event, _q("eventIdentifier"))
    etree.SubElement(ident, _q("eventIdentifierType")).text = "UUID"
    etree.SubElement(ident, _q("eventIdentifierValue")).text = ev.identifier
    etree.SubElement(event, _q("eventType")).text = ev.type
    etree.SubElement(event, _q("eventDateTime")).text = ev.date_time

    info = etree.SubElement(event, _q("eventOutcomeInformation"))
    etree.SubElement(info, _q("eventOutcome")).text = ev.outcome
    if ev.detail_note:
        detail = etree.SubElement(info, _q("eventOutcomeDetail"))
        etree.SubElement(detail, _q("eventOutcomeDetailNote")).text = ev.detail_note

    for aid in ev.agent_ids:
        linking = etree.SubElement(event, _q("linkingAgentIdentifier"))
        etree.SubElement(linking, _q("linkingAgentIdentifierType")).text = "preservation-system"
        etree.SubElement(linking, _q("linkingAgentIdentifierValue")).text = aid

    linking = etree.SubElement(event, _q("linkingObjectIdentifier"))
    etree.SubElement(linking, _q("linkingObjectIdentifierType")).text = OBJECT_ID_TYPE
    etree.SubElement(linking, _q("linkingObjectIdentifierValue")).text = target


def _append_agent(parent: etree._Element, a: PremisAgent) -> None:
    agent = etree.SubElement(parent, _q("agent"))
    ident = etree.SubElement(agent, _q("agentIdentifier"))
    etree.SubElement(ident, _q("agentIdentifierType")).text = "preservation-system"
    etree.SubElement(ident, _q("agentIdentifierValue")).text = a.id
    etree.SubElement(agent, _q("agentName")).text = a.name
    etree.SubElement(agent, _q("agentType")).text = a.kind.value


# --------------------------------------------------------------------------
# 読み戻し（AIP 作成・確認画面）
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ParsedProvenance:
    events: dict[str, list[PremisEvent]]  # objects/ からの相対パス → event（書かれた順）
    agents: list[PremisAgent]


def parse(data: bytes) -> ParsedProvenance:
    doc = etree.fromstring(data, _SAFE_PARSER)

    agents: list[PremisAgent] = []
    for el in doc.iterfind(_q("agent")):
        aid = _text(el, "agentIdentifier/premis:agentIdentifierValue")
        if not aid:
            continue
        try:
            kind = AgentKind(_text(el, "agentType") or "software")
        except ValueError:
            kind = AgentKind.SOFTWARE
        agents.append(PremisAgent(aid, _text(el, "agentName") or aid, kind))

    events: dict[str, list[PremisEvent]] = {}
    for el in doc.iterfind(_q("event")):
        target = _text(el, "linkingObjectIdentifier/premis:linkingObjectIdentifierValue")
        if not target:
            continue
        events.setdefault(target, []).append(
            PremisEvent(
                type=_text(el, "eventType"),
                date_time=_text(el, "eventDateTime"),
                detail_note=_text(el, "eventOutcomeInformation/premis:eventOutcomeDetail/"
                                      "premis:eventOutcomeDetailNote"),
                outcome=_text(el, "eventOutcomeInformation/premis:eventOutcome"),
                agent_ids=[
                    (v.text or "").strip()
                    for v in el.iterfind(
                        f"{_q('linkingAgentIdentifier')}/{_q('linkingAgentIdentifierValue')}"
                    )
                ],
                identifier=_text(el, "eventIdentifier/premis:eventIdentifierValue")
                or str(_uuid.uuid4()),
            )
        )
    return ParsedProvenance(events=events, agents=agents)


def _text(node: etree._Element, path: str) -> str:
    found = node.find(f"premis:{path}", namespaces={"premis": PREMIS_NS})
    return (found.text or "").strip() if found is not None and found.text else ""
