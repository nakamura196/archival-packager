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

## 木を丸ごとは持たない（2026-09-12）

lxml で組むこと自体は変えていないが、**AIP 全体を 1 本の木にしてから
`etree.tostring` する**のをやめた。50,000 件の実測で、この段だけで
1,449 MiB を使っていた（全体のピークの 78%）。木と、そこから作ったバイト列が
同時にメモリに載るためである（docs/performance.md）。

いまはセクション（metsHdr / dmdSec / amdSec / fileSec / structMap）を
1 つずつ組んでは直列化し、書き出して捨てる。**後ろのセクションが前のセクションを
参照しないので、この順に流せる。** 3 セクションで一致させる必要のある
fileID・admID は、ファイルの並び順から同じ式で何度でも導けるようにしてある
（`_iter_original_entries` / `_iter_derivative_entries`）。

fileSec と structMap は、セクションが 1 つしか無いのに中身が件数に比例する。
こちらは親要素を開いたまま子要素を数百件ずつ流す（`_NestedWriter`）。

バイト列を戻り値で受け取る `build_mets` では、**戻り値そのもの**（50,000 件で
約 284 MiB）が残る。これを避けたい呼び出し元のために、ファイルへ直接流す
`write_mets` を用意してある（50,000 件の実測で +15.9 MiB）。

**`etree.xmlfile`（lxml の逐次書き出し）は使っていない。** 試した結果、
出力が 1 バイト単位では一致しなかった。(1) 親で宣言済みの名前空間を
書き出す要素ごとに `ns0:` として再宣言する。(2) インデントの深さを
引き継がないので、入れ子のセクションが桁揃えされない。METS は AIP に
そのまま収められ、BagIt manifest のハッシュ対象になる来歴記録なので、
**見た目が変わるだけの差でも出してはいけない。** そこで、セクションを
`mets:mets` の入れ物に入れて直列化し、入れ物の開始タグ・終了タグの分だけ
切り落として流す方式にした（`_SectionSink`）。名前空間もインデントも
「1 本の木にしたとき」と同じ経路で決まるため、結果が一致する。

同一性は `tests/test_mets_streaming.py` が、旧実装
（`_build_mets_in_memory`。参照用に残してある）との突合で固定している。
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from itertools import chain, islice
from typing import BinaryIO

from lxml import etree

from .aip_models import AgentKind, AIPFile, DescriptiveMetadata, PremisAgent, PremisEvent

#: 親要素を受け取って新しい子要素を返すもの（`_NestedWriter` が使う）。
_Factory = Callable[[etree._Element], etree._Element]
#: 親要素を受け取って、その下に子要素を組むもの。
_Builder = Callable[[etree._Element], None]

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


class _SectionSink:
    """METS 直下のセクションを 1 つずつ直列化して書き出す。

    セクションは `mets:mets` の入れ物（holder）に入れた状態で直列化する。
    こうすると、名前空間の宣言は入れ物の側に載り、インデントも深さ 1 として
    付く。**1 本の木にしてから直列化したときと同じ経路**を通るので、
    出したバイト列を並べれば元と一致する。入れ物の開始タグと終了タグの分は
    切り落として流す。

    開始タグ・終了タグの綴りは決め打ちにせず、空の入れ物を 1 度だけ
    直列化して採る。lxml の版が上がって引用符の種類や属性の並びが変わっても、
    切り出しと実際の出力がずれないようにするため。
    """

    def __init__(self, out: BinaryIO) -> None:
        self._out = out
        probe = etree.Element(_q(METS_NS, "mets"), nsmap=NSMAP)
        # 子が無いと自己閉じタグ（<mets:mets/>）になり開始・終了タグを採れない。
        etree.SubElement(probe, "probe")
        lines = etree.tostring(
            probe, xml_declaration=True, encoding="UTF-8", pretty_print=True
        ).split(b"\n")
        self._declaration = lines[0] + b"\n"
        self._open_tag = lines[1] + b"\n"
        self._close_tag = lines[3] + b"\n"

    def start(self) -> None:
        self._out.write(self._declaration)
        self._out.write(self._open_tag)

    def holder(self) -> etree._Element:
        """セクションを組むための入れ物。既存の `_append_*` に親として渡す。"""
        return etree.Element(_q(METS_NS, "mets"), nsmap=NSMAP)

    def section_bytes(self, holder: etree._Element) -> bytes:
        """入れ物の中身だけをバイト列で返す。"""
        # encoding を省くと非 ASCII が &#12354; のような数値参照になる。
        # 資料名に日本語が入るのが普通なので、文書全体と同じ UTF-8 で出す。
        blob = etree.tostring(
            holder, encoding="UTF-8", xml_declaration=False, pretty_print=True
        )
        if not (blob.startswith(self._open_tag) and blob.endswith(self._close_tag)):
            # ここが破れると METS が壊れた形で出る。黙って通さない。
            raise RuntimeError("METS のセクションを切り出せませんでした")
        return blob[len(self._open_tag) : len(blob) - len(self._close_tag)]

    def raw(self, blob: bytes) -> None:
        self._out.write(blob)

    def flush(self, holder: etree._Element) -> None:
        """入れ物の中身だけを書き出す。holder はこの後捨てられる。"""
        self._out.write(self.section_bytes(holder))

    def finish(self) -> None:
        self._out.write(self._close_tag)


#: 1 度に組む子要素の数。fileSec と structMap は「1 ファイル = 1 要素」なので、
#: セクション全体を組むと結局ファイル数に比例した木を持つことになる。
#: ここで区切って、常駐をこの件数分に抑える。大きすぎると効果が薄れ、
#: 小さすぎると親の入れ子を組み直す回数が増える。512 は実測で頭打ちの位置。
_BATCH = 512


class _NestedWriter:
    """入れ子の親要素を開いたまま、子要素を少しずつ流す。

    `_SectionSink` はセクション 1 つを丸ごと組むので、fileSec と structMap
    （どちらも 1 ファイルにつき要素が 2 つできる）には足りない。ここでは
    親要素（fileSec / fileGrp / structMap / div）を「開いた」状態にして、
    子要素だけを区切って書き出す。

    **インデントや閉じタグを自前で組み立てない。** 開いている親をその都度
    組み直し、目印の子を 1 つ入れて直列化し、目印の前後を開始側・終了側として
    採る。そのため桁揃えも名前空間も lxml が決めた通りになり、1 本の木にして
    直列化した結果と一致する。組み直す親は数個なので、費用は無視できる。
    """

    def __init__(self, sink: _SectionSink) -> None:
        self._sink = sink
        self._factories: list[_Factory] = []
        self._head = b""  # 開いている親の開始タグ群（累積）
        self._tail = b""  # 同じく終了タグ群（累積）
        self._saved: list[tuple[bytes, bytes]] = []

    def _chain(self, holder: etree._Element) -> etree._Element:
        inner = holder
        for factory in self._factories:
            inner = factory(inner)
        return inner

    def open(self, factory: _Factory) -> None:
        """親要素を 1 つ開く。factory は親を受け取って新しい子を返す。"""
        self._saved.append((self._head, self._tail))
        self._factories.append(factory)

        holder = self._sink.holder()
        etree.SubElement(self._chain(holder), "probe")
        blob = self._sink.section_bytes(holder)
        marker = b"<probe/>\n"
        at = blob.index(marker)
        line_start = blob.rindex(b"\n", 0, at) + 1  # 目印の行頭（インデントの手前）
        head, tail = blob[:line_start], blob[at + len(marker) :]

        self._sink.raw(head[len(self._head) :])  # 今回増えた開始タグだけ
        self._head, self._tail = head, tail

    def write(self, build: _Builder) -> None:
        """いま開いている親の直下に子要素を組んで書き出す。"""
        holder = self._sink.holder()
        build(self._chain(holder))
        blob = self._sink.section_bytes(holder)
        if not (blob.startswith(self._head) and blob.endswith(self._tail)):
            raise RuntimeError("METS の子要素を切り出せませんでした")
        self._sink.raw(blob[len(self._head) : len(blob) - len(self._tail)])

    def close(self) -> None:
        head, tail = self._saved.pop()
        self._sink.raw(self._tail[: len(self._tail) - len(tail)])  # 今回閉じる分だけ
        self._factories.pop()
        self._head, self._tail = head, tail


def build_mets(
    *,
    aip_uuid: str,
    files: list[AIPFile],
    agents: list[PremisAgent],
    descriptive: DescriptiveMetadata | None,
    created_iso: str,
    submission_documentation: list[SubmissionDocument] | None = None,
) -> bytes:
    """METS を組み立てて UTF-8 のバイト列で返す。

    バイト列を一括で受け取りたい呼び出し元のための包み。**AIP の大きさに
    比例したバイト列がそのまま戻り値になる**ので、数万件を扱うときは
    `write_mets` で直接ファイルへ流すほうがメモリを使わない。
    """
    buf = io.BytesIO()
    write_mets(
        buf,
        aip_uuid=aip_uuid,
        files=files,
        agents=agents,
        descriptive=descriptive,
        created_iso=created_iso,
        submission_documentation=submission_documentation,
    )
    return buf.getvalue()


def write_mets(
    out: BinaryIO,
    *,
    aip_uuid: str,
    files: list[AIPFile],
    agents: list[PremisAgent],
    descriptive: DescriptiveMetadata | None,
    created_iso: str,
    submission_documentation: list[SubmissionDocument] | None = None,
) -> None:
    """METS をセクションごとに組んでは `out` へ流す。

    出力は `build_mets` と 1 バイトも違わない。違うのは、AIP 全体の木を
    一度に持たないこと。数万件の AIP ではここが常駐メモリの最大の山になる
    （docs/performance.md）。
    """
    sink = _SectionSink(out)
    sink.start()

    holder = sink.holder()
    etree.SubElement(
        holder, _q(METS_NS, "metsHdr"), CREATEDATE=created_iso, RECORDSTATUS="complete"
    )
    sink.flush(holder)

    # dmdSec。SIP 全体と file 単位の両方。
    whole_dmd_id = "dmdSec_whole"
    has_whole_dmd = descriptive is not None and descriptive.has_any
    if has_whole_dmd:
        holder = sink.holder()
        _append_dmd_sec(holder, descriptive, whole_dmd_id)
        sink.flush(holder)

    # structMap で使うため、file 単位の dmdSec の ID だけは覚えておく。
    # 記述メタデータを持つファイルは通常ごく一部なので、常駐しても小さい。
    file_dmd_ids: dict[str, str] = {}
    for n, f in enumerate(files):
        if f.descriptive is not None and f.descriptive.has_any:
            dmd_id = f"dmdSec_obj{n}"
            holder = sink.holder()
            _append_dmd_sec(holder, f.descriptive, dmd_id)
            sink.flush(holder)
            file_dmd_ids[f.relative_path] = dmd_id

    # amdSec: まず agent をまとめて 1 つ、続いて各オブジェクト。
    agent_kinds = {a.id: a.kind for a in agents}
    holder = sink.holder()
    _append_agents_amd_sec(holder, agents)
    sink.flush(holder)

    # 各原本・派生物の amdSec。**1 件組んでは捨てる。** admID・fileID は
    # ここで持ち回らず、fileSec 側で同じ式から導き直す（_iter_original_entries）。
    for n, f in enumerate(files):
        holder = sink.holder()
        _append_object_amd_sec(
            holder,
            adm_id=f"amdSec_{n}",
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
        sink.flush(holder)

        for m, d in enumerate(f.derivatives):
            holder = sink.holder()
            _append_object_amd_sec(
                holder,
                adm_id=f"amdSec_{n}_d{m}",
                object_uuid=d.uuid,
                sha256=d.sha256,
                size=d.size_bytes,
                format_name=d.format_name_out,
                puid=d.puid_out,
                original_name=None,
                related_uuid=f.uuid,
                relationship_subtype="is normalized version of",
                events=[],
                agent_kinds=agent_kinds,
            )
            sink.flush(holder)

    # fileSec / structMap。どちらも 1 ファイルにつき要素が 2 つできるので、
    # セクション単位ではなく子要素を区切って流す。
    _write_file_sec(sink, files, submission_documentation)
    _write_struct_map(sink, files, whole_dmd_id if has_whole_dmd else None, file_dmd_ids)

    sink.finish()


def _build_mets_in_memory(
    *,
    aip_uuid: str,
    files: list[AIPFile],
    agents: list[PremisAgent],
    descriptive: DescriptiveMetadata | None,
    created_iso: str,
    submission_documentation: list[SubmissionDocument] | None = None,
) -> bytes:
    """**参照用。本番経路では使わない（テストからのみ呼ぶ）。**

    AIP 全体を 1 本の木にしてから直列化する、2026-09-12 以前の実装。
    逐次書き出し版の出力がこれと 1 バイトも違わないことを
    `tests/test_mets_streaming.py` が確かめるために残してある。
    **消さないこと。** 消すと「出力が変わっていない」ことを機械で言えなくなる。
    """
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
                format_name=d.format_name_out,
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


def _batches(items: Iterator, size: int) -> Iterator[list]:
    """iterator を size 件ずつの塊にする。塊が空になったら終わり。"""
    while True:
        batch = list(islice(items, size))
        if not batch:
            return
        yield batch


def _iter_original_entries(files: list[AIPFile]) -> Iterator[dict]:
    """原本の fileSec 項目。**amdSec を組んだときと同じ式で admID を導く。**

    逐次書き出しでは amdSec を組み終えた時点でその木を捨てているので、
    ID を持ち回れない。持ち回る代わりに、並び順から何度でも同じ ID を
    導けるようにしてある（ここがずれると ADMID が実在しない amdSec を指す。
    tests/test_mets_streaming.py が旧実装との突合で固定している）。
    """
    for n, f in enumerate(files):
        yield {
            "file_id": f"file-{f.uuid}",
            "adm_id": f"amdSec_{n}",
            "href": f"objects/{f.relative_path}",
        }


def _iter_derivative_entries(files: list[AIPFile]) -> Iterator[dict]:
    """派生物の fileSec 項目。admID の導き方は `_iter_original_entries` と同じ。"""
    for n, f in enumerate(files):
        for m, d in enumerate(f.derivatives):
            yield {
                "file_id": f"file-{d.uuid}",
                "adm_id": f"amdSec_{n}_d{m}",
                "href": f"objects/{d.relative_path}",
            }


def _write_file_sec(
    sink: _SectionSink,
    files: list[AIPFile],
    submission_documentation: list[SubmissionDocument] | None,
) -> None:
    """fileSec を、fileGrp を開いたまま項目を区切って書き出す。"""
    writer = _NestedWriter(sink)
    writer.open(lambda parent: etree.SubElement(parent, _q(METS_NS, "fileSec")))

    _write_file_grp(writer, "original", _iter_original_entries(files))
    _write_file_grp(
        writer, "preservation", _iter_derivative_entries(files), omit_if_empty=True
    )

    # **提出書類も METS に載せる。** AIP に入れているのに fileSec に無いと、
    # METS だけを読む側からは存在しないことになる。Archivematica も
    # submissionDocumentation の fileGrp を持つ。
    if submission_documentation:
        _write_file_grp(
            writer,
            "submissionDocumentation",
            (
                {"file_id": f"file-{d.uuid}", "adm_id": None, "href": d.href}
                for d in submission_documentation
            ),
        )

    writer.close()


def _write_file_grp(
    writer: _NestedWriter,
    use: str,
    entries: Iterable[dict],
    *,
    omit_if_empty: bool = False,
) -> None:
    items = iter(entries)
    first = list(islice(items, 1))
    if not first:
        if omit_if_empty:
            return
        # 中身が無い fileGrp は自己閉じタグ（<mets:fileGrp USE="original"/>）に
        # なる。開いてから閉じると <...></...> の形になり、1 本の木にした
        # ときと出力が変わってしまう。**空のときだけは子として 1 つ書く。**
        writer.write(
            lambda parent: etree.SubElement(parent, _q(METS_NS, "fileGrp"), USE=use)
        )
        return

    writer.open(lambda parent: etree.SubElement(parent, _q(METS_NS, "fileGrp"), USE=use))
    for batch in _batches(chain(first, items), _BATCH):

        def build(parent: etree._Element, batch: list[dict] = batch) -> None:
            for entry in batch:
                _append_file(parent, entry)

        writer.write(build)
    writer.close()


def _append_file_grp(parent: etree._Element, use: str, entries: Iterable[dict]) -> None:
    """fileGrp を 1 つ足す。"""
    grp = etree.SubElement(parent, _q(METS_NS, "fileGrp"), USE=use)
    for e in entries:
        _append_file(grp, e)


def _append_file(parent: etree._Element, entry: dict) -> None:
    """fileSec の 1 項目。adm_id が None の項目には ADMID を付けない
    （提出書類は PREMIS の object を持たない）。"""
    attrs = {"ID": str(entry["file_id"])}
    if entry.get("adm_id"):
        attrs["ADMID"] = str(entry["adm_id"])
    f = etree.SubElement(parent, _q(METS_NS, "file"), **attrs)
    flocat = etree.SubElement(
        f, _q(METS_NS, "FLocat"), LOCTYPE="OTHER", OTHERLOCTYPE="SYSTEM"
    )
    flocat.set(_q(XLINK_NS, "href"), str(entry["href"]))


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
        _append_item_div(parent, name, file_id, dmd_id)


def _append_item_div(
    parent: etree._Element, name: str, file_id: str, dmd_id: str | None
) -> None:
    """structMap の 1 ファイル分（div と、fileSec への参照）。"""
    attrs = {"TYPE": "item", "LABEL": name}
    if dmd_id:
        attrs["DMDID"] = dmd_id
    div = etree.SubElement(parent, _q(METS_NS, "div"), **attrs)
    etree.SubElement(div, _q(METS_NS, "fptr"), FILEID=file_id)


def _build_node_tree(files: list[AIPFile], file_dmd_ids: dict[str, str]) -> _Node:
    """相対パスからディレクトリの木を作る。

    lxml の要素より、この木のほうがずっと軽い（1 ファイルにつきタプル 1 つ）。
    div を組むのは書き出す直前まで待つ。
    """
    root_node = _Node()
    for f in files:
        comps = f.relative_path.split("/")
        name = comps.pop()
        cur = root_node
        for d in comps:
            cur = cur.dirs.setdefault(d, _Node())
        cur.files.append((name, f"file-{f.uuid}", file_dmd_ids.get(f.relative_path)))
    return root_node


def _write_struct_map(
    sink: _SectionSink,
    files: list[AIPFile],
    dmd_id: str | None,
    file_dmd_ids: dict[str, str],
) -> None:
    """structMap を、div を開いたまま項目を区切って書き出す。"""
    root_node = _build_node_tree(files, file_dmd_ids)

    writer = _NestedWriter(sink)
    writer.open(
        lambda parent: etree.SubElement(parent, _q(METS_NS, "structMap"), TYPE="physical")
    )
    attrs = {"TYPE": "directory", "LABEL": "objects"}
    if dmd_id:
        attrs["DMDID"] = dmd_id

    if not root_node.dirs and not root_node.files:
        # ファイルが 1 件も無いときの div は自己閉じタグになる（`_write_file_grp`
        # の空の fileGrp と同じ事情）。開かずに子として 1 つ書く。
        writer.write(lambda parent: etree.SubElement(parent, _q(METS_NS, "div"), **attrs))
    else:
        writer.open(lambda parent: etree.SubElement(parent, _q(METS_NS, "div"), **attrs))
        _write_node(writer, root_node)
        writer.close()
    writer.close()


def _write_node(writer: _NestedWriter, node: _Node) -> None:
    # 並び順を固定する。dict の挿入順に任せると入力の走査順で METS が変わり、
    # 同じ資料から 2 回作った AIP を突合できなくなる。
    # ディレクトリを先に、次にファイル。`_render_node` と同じ順でなければならない。
    for name in sorted(node.dirs):
        writer.open(
            lambda parent, name=name: etree.SubElement(
                parent, _q(METS_NS, "div"), TYPE="directory", LABEL=name
            )
        )
        _write_node(writer, node.dirs[name])
        writer.close()

    items = iter(sorted(node.files, key=lambda t: t[0]))
    for batch in _batches(items, _BATCH):

        def build(parent: etree._Element, batch: list = batch) -> None:
            for name, file_id, dmd_id in batch:
                _append_item_div(parent, name, file_id, dmd_id)

        writer.write(build)
