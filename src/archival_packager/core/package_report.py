"""できあがった情報パッケージを、人が読める形にまとめる。

なぜ要るか
----------
ファイルの中身を見るだけなら Finder やエクスプローラーのほうが便利で、
原本（PDF・Word・画像）はどのみちアプリの中では開けない。
**アプリにしか出せないのは、METS と PREMIS の中身のほう。**
XML を開いても担当者には読めないので、ここで表に直す。

  1. 概要   — 何がいくつ入っているか、いつ作ったか
  2. 処理の記録 — いつ・何を・どのツールで行い、結果はどうだったか（PREMIS event）
  3. ファイル一覧 — フォーマット・PRONOM・サイズ・SHA-256・ウイルス検査

AIP は METS を読む。SIP は METS を持たないので、metadata/formats.csv
（技術インベントリ）と description.csv から同じ形を作る。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

METS_NS = "http://www.loc.gov/METS/"
PREMIS_NS = "http://www.loc.gov/premis/v3"
XLINK_NS = "http://www.w3.org/1999/xlink"
DC_NS = "http://purl.org/dc/elements/1.1/"

#: PREMIS の eventType は英語で書く決まりなので、表示のときだけ日本語にする。
EVENT_LABELS = {
    "ingestion": "取り込み",
    "fixity check": "完全性の確認",
    "format identification": "フォーマットの識別",
    "virus check": "ウイルス検査",
    "normalization": "保存用形式への変換",
    "message digest calculation": "チェックサムの算出",
}

#: fileGrp USE の意味。
USE_LABELS = {
    "original": "原本",
    "preservation": "保存用",
    "submissionDocumentation": "提出書類",
}


@dataclass(frozen=True)
class Overview:
    kind: str = ""            # "AIP" / "SIP"
    title: str = ""
    identifier: str = ""
    created: str = ""
    file_count: int = 0
    original_count: int = 0   # そのうち原本
    total_bytes: int = 0
    note: str = ""            # 読めなかったときの説明


@dataclass(frozen=True)
class EventRow:
    date_time: str = ""
    type_label: str = ""
    outcome: str = ""
    detail: str = ""
    agent: str = ""
    target: str = ""          # 対象のファイル。パッケージ全体なら空


@dataclass(frozen=True)
class FileRow:
    path: str = ""
    use: str = ""
    format_name: str = ""
    puid: str = ""
    size: int = 0
    sha256: str = ""
    virus: str = ""
    warning: str = ""


@dataclass(frozen=True)
class Summary:
    """まとまりで見たときの数。**1 件ずつ並べても全体は掴めない。**

    どの形式がどれだけ入っているか、変換したものが何件あるか、
    確認が要るものが何件あるか。ここが分かると、担当者は次に何を
    見ればよいかを決められる。
    """

    formats: list[tuple[str, int]] = field(default_factory=list)   # 多い順
    events: list[tuple[str, int]] = field(default_factory=list)    # 処理の種類ごと
    normalized: int = 0        # 保存用形式に変換したもの
    unidentified: int = 0      # フォーマットを特定できなかったもの
    extension_warnings: int = 0  # 拡張子と中身が食い違うもの
    virus_scanned: int = 0     # ウイルス検査を行ったもの


@dataclass(frozen=True)
class PackageReport:
    root: Path
    overview: Overview
    events: list[EventRow] = field(default_factory=list)
    files: list[FileRow] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)
    mets_path: Path | None = None


def find_mets(root: Path) -> Path | None:
    """METS を探す。bag なら data/ の下にある。"""
    for base in (root / "data", root):
        if not base.is_dir():
            continue
        for name in sorted(p.name for p in base.glob("METS*.xml")):
            return base / name
    return None


def read(root: Path) -> PackageReport:
    """パッケージのディレクトリを読んでまとめる。読めない部分は空で返す。"""
    mets_path = find_mets(root)
    if mets_path is not None:
        try:
            return _read_mets(root, mets_path)
        except (OSError, etree.XMLSyntaxError) as exc:
            return PackageReport(
                root=root,
                overview=Overview(kind="AIP", note=f"METS を読めませんでした: {exc}"),
                mets_path=mets_path,
            )
    return _read_sip(root)


# --------------------------------------------------------------------------
# AIP（METS を読む）
# --------------------------------------------------------------------------


def _text(node, path: str) -> str:
    found = node.find(path, namespaces={"premis": PREMIS_NS, "dc": DC_NS})
    return (found.text or "").strip() if found is not None else ""


def _read_mets(root: Path, mets_path: Path) -> PackageReport:
    tree = etree.parse(str(mets_path))
    doc = tree.getroot()
    ns = {"mets": METS_NS, "premis": PREMIS_NS, "xlink": XLINK_NS, "dc": DC_NS}

    # metsHdr
    created = ""
    hdr = doc.find("mets:metsHdr", namespaces=ns)
    if hdr is not None:
        created = hdr.get("CREATEDATE", "")

    # dmdSec（パッケージ全体）
    title = identifier = ""
    whole = doc.find("mets:dmdSec[@ID='dmdSec_whole']", namespaces=ns)
    if whole is not None:
        title = _text(whole, f".//{{{DC_NS}}}title")
        identifier = _text(whole, f".//{{{DC_NS}}}identifier")

    # agent（event から参照される）
    agents: dict[str, str] = {}
    for agent in doc.iterfind(f".//{{{PREMIS_NS}}}agent"):
        aid = _text(agent, f"{{{PREMIS_NS}}}agentIdentifier/{{{PREMIS_NS}}}agentIdentifierValue")
        name = _text(agent, f"{{{PREMIS_NS}}}agentName")
        if aid:
            agents[aid] = name or aid

    # fileSec: ADMID → (href, USE)
    location: dict[str, tuple[str, str]] = {}
    for grp in doc.iterfind(f".//{{{METS_NS}}}fileGrp"):
        use = USE_LABELS.get(grp.get("USE", ""), grp.get("USE", ""))
        for f in grp.iterfind(f"{{{METS_NS}}}file"):
            flocat = f.find(f"{{{METS_NS}}}FLocat")
            href = flocat.get(f"{{{XLINK_NS}}}href", "") if flocat is not None else ""
            adm = f.get("ADMID")
            if adm:
                location[adm] = (href, use)
            elif href:
                location[f"file:{href}"] = (href, use)

    files: list[FileRow] = []
    events: list[EventRow] = []
    total = 0

    for amd in doc.iterfind(f"{{{METS_NS}}}amdSec"):
        amd_id = amd.get("ID", "")
        if amd_id == "amdSec_agents":
            continue
        href, use = location.get(amd_id, ("", ""))

        obj = amd.find(f".//{{{PREMIS_NS}}}object")
        if obj is not None:
            size_text = _text(obj, f".//{{{PREMIS_NS}}}size")
            size = int(size_text) if size_text.isdigit() else 0
            total += size
            files.append(
                FileRow(
                    path=href or _text(obj, f"{{{PREMIS_NS}}}originalName"),
                    use=use,
                    format_name=_text(obj, f".//{{{PREMIS_NS}}}formatName"),
                    puid=_text(obj, f".//{{{PREMIS_NS}}}formatRegistryKey"),
                    size=size,
                    sha256=_text(obj, f".//{{{PREMIS_NS}}}messageDigest"),
                )
            )

        for ev in amd.iterfind(f".//{{{PREMIS_NS}}}event"):
            events.append(_event_row(ev, agents, href))

    # 提出書類のように PREMIS を持たないものも一覧には出す。
    # サイズは METS に無いので、実物から測る（無ければ 0 のまま）。
    listed = {f.path for f in files}
    for _key, (href, use) in location.items():
        if href and href not in listed:
            files.append(FileRow(path=href, use=use, size=_size_on_disk(root, href)))

    events.sort(key=lambda e: e.date_time)
    files = _merge_inherited_csv(root, files)

    return PackageReport(
        root=root,
        summary=_summarize(files, events),
        overview=Overview(
            kind="AIP",
            title=title,
            identifier=identifier,
            created=created,
            file_count=len(files),
            original_count=sum(1 for f in files if f.use == "原本"),
            total_bytes=total,
        ),
        events=events,
        files=files,
        mets_path=mets_path,
    )


def _merge_inherited_csv(root: Path, files: list[FileRow]) -> list[FileRow]:
    """AIP に引き継がれた formats.csv から、METS に無い列を補う。

    ウイルス検査の結果と拡張子警告は METS では表せない
    （PREMIS の event と outcome には出るが、ファイル単位の表にはならない）。
    SIP 段の技術インベントリが提出書類として AIP に入っているので、そこから拾う。
    """
    metadata = _find_metadata_dir(root)
    if metadata is None:
        for base in (root / "data" / "objects" / "submissionDocumentation",
                     root / "objects" / "submissionDocumentation"):
            if (base / "formats.csv").is_file():
                metadata = base
                break
    if metadata is None:
        return files

    inherited: dict[str, dict[str, str]] = {}
    for row in _rows(metadata / "formats.csv"):
        rel = (row.get("相対パス") or "").strip()
        if rel:
            inherited[rel] = row

    merged: list[FileRow] = []
    for f in files:
        # METS の href は objects/ から始まる。CSV は原本からの相対。
        key = f.path.split("objects/", 1)[-1] if "objects/" in f.path else f.path
        row = inherited.get(key)
        if row is None:
            merged.append(f)
            continue
        merged.append(
            FileRow(
                path=f.path, use=f.use, format_name=f.format_name, puid=f.puid,
                size=f.size, sha256=f.sha256,
                virus=(row.get("ウイルス検査") or "").strip(),
                warning=(row.get("拡張子警告") or "").strip(),
            )
        )
    return merged


def _size_on_disk(root: Path, href: str) -> int:
    for candidate in (root / href, root / "data" / href):
        try:
            if candidate.is_file():
                return candidate.stat().st_size
        except OSError:
            continue
    return 0


def _event_row(ev, agents: dict[str, str], target: str) -> EventRow:
    kind = _text(ev, f"{{{PREMIS_NS}}}eventType")
    linked = [
        (e.text or "").strip()
        for e in ev.iterfind(
            f"{{{PREMIS_NS}}}linkingAgentIdentifier/{{{PREMIS_NS}}}linkingAgentIdentifierValue"
        )
    ]
    return EventRow(
        date_time=_text(ev, f"{{{PREMIS_NS}}}eventDateTime"),
        type_label=EVENT_LABELS.get(kind, kind),
        outcome=_text(ev, f".//{{{PREMIS_NS}}}eventOutcome"),
        detail=_text(ev, f".//{{{PREMIS_NS}}}eventOutcomeDetailNote"),
        agent="、".join(agents.get(a, a) for a in linked),
        target=target,
    )


# --------------------------------------------------------------------------
# SIP（METS が無いので CSV から作る）
# --------------------------------------------------------------------------


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text, newline="")))


#: SIP の metadata/ の中で、CSV が置かれる場所。SIP Creator に倣って
#: submissionDocumentation/ の下に入る（BagIt 化すると data/ が 1 段挟まる）。
_SIP_METADATA_DIRS = (
    Path("metadata") / "submissionDocumentation",
    Path("metadata"),
    Path("data") / "metadata" / "submissionDocumentation",
    Path("data") / "metadata",
)


def _find_metadata_dir(root: Path) -> Path | None:
    for rel in _SIP_METADATA_DIRS:
        if (root / rel / "formats.csv").is_file():
            return root / rel
    return None


def _read_sip(root: Path) -> PackageReport:
    metadata = _find_metadata_dir(root) or root / "metadata"
    formats = _rows(metadata / "formats.csv")

    files: list[FileRow] = []
    total = 0
    for row in formats:
        size_text = (row.get("サイズ(バイト)") or "").strip()
        size = int(size_text) if size_text.isdigit() else 0
        total += size
        files.append(
            FileRow(
                path=(row.get("相対パス") or "").strip(),
                use="原本",
                format_name=(row.get("フォーマット名") or "").strip(),
                puid=(row.get("PRONOM") or "").strip(),
                size=size,
                sha256=(row.get("SHA-256") or "").strip(),
                virus=(row.get("ウイルス検査") or "").strip(),
                warning=(row.get("拡張子警告") or "").strip(),
            )
        )

    title = identifier = ""
    description = _rows(metadata / "description.csv")
    if description:
        first = description[0]
        title = (first.get("Title") or "").strip()
        identifier = (first.get("Identifier") or "").strip()

    note = "" if files else "技術インベントリ（formats.csv）が見つかりませんでした"
    summary = _summarize(files, [])
    return PackageReport(
        root=root,
        summary=summary,
        overview=Overview(
            kind="SIP",
            title=title,
            identifier=identifier,
            file_count=len(files),
            original_count=len(files),
            total_bytes=total,
            note=note,
        ),
        files=files,
    )


def _summarize(files: list[FileRow], events: list[EventRow]) -> Summary:
    originals = [f for f in files if f.use in ("原本", "")]

    formats: dict[str, int] = {}
    for f in originals:
        name = f.format_name or "（未識別）"
        formats[name] = formats.get(name, 0) + 1

    kinds: dict[str, int] = {}
    for e in events:
        kinds[e.type_label] = kinds.get(e.type_label, 0) + 1

    return Summary(
        # 多い順。同数なら名前順にして、開くたびに並びが変わらないようにする。
        formats=sorted(formats.items(), key=lambda kv: (-kv[1], kv[0])),
        events=sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])),
        normalized=sum(1 for f in files if f.use == "保存用"),
        unidentified=sum(1 for f in originals if not f.puid),
        extension_warnings=sum(
            1 for f in originals if f.warning and f.warning not in ("-", "なし")
        ),
        virus_scanned=sum(
            1 for f in originals if f.virus and f.virus not in ("", "未実施", "-")
        ),
    )


def human_bytes(size: int) -> str:
    """表示用。1 KB = 1024 で数える。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
