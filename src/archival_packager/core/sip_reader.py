"""入力 SIP の読み取り。

退役した Swift 実装の `Sources/AIP/SIPReader.swift` に由来する。

SIP（または bag）を走査して、objects/ 配下の原本一覧を AIPFile として返す。
SHA-256 とフォーマット識別(PUID) は SIP 段の成果物から**継承**する。

    第一候補    submissionDocumentation/formats.csv
    次点        manifest-sha256.txt / checksum.sha256 の objects 行（SHA-256 のみ）
    どちらも無し 実ファイルから SHA-256 を再計算し、PUID は未識別とする

継承するのは、SIP 段で識別・計算した結果を AIP 段で作り直さないため。
作り直すと「受入時の記録」と「保存時の記録」が食い違う余地が生まれる。

## 旧形式の SIP も必ず読めること（2026-09-12）

このアプリは公開済みで、利用者の手元には旧形式のパッケージがある。
**読めなくなると、過去に作った SIP から AIP を作れなくなる。** 相互運用の是正で
次の 2 つが変わったので、ここでは新旧どちらの書き方も受け付ける。

    checksum.sha256   旧 metadata/submissionDocumentation/ に "<hash>  objects/<rel>"
                      新 metadata/ 直下に "<hash>  <rel>"（objects/ からの相対）
    metadata.csv      旧 filename が "objects/" と "objects/<rel>"
                      新 filename が "objects" と "objects/<rel>"、
                         bag では "data/objects" と "data/objects/<rel>"

`description.csv` の列見出しは**変えていない**（人が書き込むシートであり、
ここが読み戻しの土台になっている）。AtoM へ渡す機械名の CSV は
atom-import.csv として別に出している。
"""

from __future__ import annotations

import csv
import io
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path

from .aip_models import AIPFile, AIPPipelineError, DescriptiveMetadata
from .checksums import sha256_of

_SUBDOC_CANDIDATES = (
    "data/metadata/submissionDocumentation",
    "metadata/submissionDocumentation",
    "data/metadata",
    "metadata",
)

_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


@dataclass(slots=True)
class ParsedSIP:
    objects_root: Path  # 原本ルート（data/objects または objects）
    files: list[AIPFile]
    submission_documentation: Path | None = None  # 継承する提出書類ディレクトリ
    descriptive: DescriptiveMetadata | None = None  # description.csv から継承
    inherited_hashes: int = 0  # SIP から継承できたハッシュ件数（レポート用）
    recomputed_hashes: int = 0  # 再計算したハッシュ件数


@dataclass(slots=True)
class _InheritedMeta:
    format_name: str | None = None
    puid: str | None = None
    mime: str | None = None
    sha256: str | None = None
    #: formats.csv の「ウイルス検査」列。古い SIP には無いので None になりうる。
    virus_state: str | None = None
    descriptive: DescriptiveMetadata | None = None


def detect_bag(sip_root: Path) -> bool:
    """BagIt bag かどうか。bagit.txt の有無で判定する。"""
    return (sip_root / "bagit.txt").is_file()


def looks_like_sip(path: Path) -> bool:
    """AIP 作成の入力になれるフォルダか。read() が受け付ける形と同じ判定。

    画面で、実行する前に確かめるために使う。素材のフォルダをそのまま
    AIP 作成に渡すと、処理を始めてから「objects/ が見つかりません」で落ちる。
    技術者でない利用者には、何を間違えたのかがその文面から分からない。
    """
    if not path.is_dir():
        return False
    objects_root = path / "data" / "objects" if detect_bag(path) else path / "objects"
    return objects_root.is_dir()


def read(sip_root: Path, *, is_bag: bool | None = None) -> ParsedSIP:
    if is_bag is None:
        is_bag = detect_bag(sip_root)

    objects_root = sip_root / "data" / "objects" if is_bag else sip_root / "objects"
    if not objects_root.is_dir():
        raise AIPPipelineError.not_a_sip(f"objects/ が見つかりません: {objects_root}")

    subdoc = _first_existing_dir(sip_root, _SUBDOC_CANDIDATES)

    meta: dict[str, _InheritedMeta] = {}
    descriptive: DescriptiveMetadata | None = None

    if subdoc is not None:
        formats_csv = subdoc / "formats.csv"
        if formats_csv.is_file():
            meta = _parse_formats_csv(_read_text(formats_csv))

        description_csv = subdoc / "description.csv"
        if description_csv.is_file():
            descriptive = _parse_description_csv(_read_text(description_csv))

        metadata_csv = _first_existing_file(
            [subdoc / "metadata.csv", subdoc.parent / "metadata.csv"]
        )
        if metadata_csv is not None:
            per_file = _parse_metadata_csv(_read_text(metadata_csv))
            for rel, d in per_file.items():
                entry = meta.setdefault(rel, _InheritedMeta())
                entry.descriptive = d
                # metadata.csv の "objects/" 行は SIP 全体の記述。
            whole = per_file.get("")
            if whole is not None:
                descriptive = DescriptiveMetadata.merge(whole, descriptive)

    # マニフェストからのハッシュ継承（formats.csv が無い場合の次点）。
    manifest_hashes = _parse_manifest(sip_root, is_bag=is_bag)

    files: list[AIPFile] = []
    inherited = 0
    recomputed = 0

    for path in sorted(objects_root.rglob("*")):
        if path.name in _EXCLUDED_NAMES:
            continue
        if path.is_symlink() or not path.is_file():
            continue

        rel = path.relative_to(objects_root).as_posix()
        # SIP 段で作られた提出書類のコピーは原本ではない。
        if rel.startswith("submissionDocumentation/"):
            continue

        entry = meta.get(rel, _InheritedMeta())
        digest = entry.sha256 or manifest_hashes.get(rel)
        if digest:
            inherited += 1
        else:
            digest = sha256_of(path)
            recomputed += 1

        files.append(
            AIPFile(
                relative_path=rel,
                absolute_path=path,
                size_bytes=path.stat().st_size,
                uuid=str(_uuid.uuid4()),
                sha256=digest,
                puid=entry.puid,
                format_name=entry.format_name,
                mime_type=entry.mime,
                virus_state=entry.virus_state,
                descriptive=entry.descriptive,
            )
        )

    if not files:
        raise AIPPipelineError.no_objects()

    return ParsedSIP(
        objects_root=objects_root,
        files=files,
        submission_documentation=subdoc,
        descriptive=descriptive,
        inherited_hashes=inherited,
        recomputed_hashes=recomputed,
    )


# --------------------------------------------------------------------------
# CSV / マニフェストの解析
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("﻿")
    except (OSError, UnicodeDecodeError):
        return ""


def _rows(text: str) -> list[list[str]]:
    if not text.strip():
        return []
    return list(csv.reader(io.StringIO(text, newline="")))


def _parse_formats_csv(text: str) -> dict[str, _InheritedMeta]:
    """formats.csv から 相対パス -> 継承メタ を作る。列見出しで引く。"""
    rows = _rows(text)
    if not rows:
        return {}

    header = rows[0]

    def col(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    i_path, i_name = col("相対パス"), col("フォーマット名")
    i_puid, i_mime, i_sha = col("PRONOM"), col("MIME"), col("SHA-256")
    # 0.1.4 で足した列。古い SIP には無いので、無ければ「不明」として扱う。
    i_virus = col("ウイルス検査")

    out: dict[str, _InheritedMeta] = {}
    for fields in rows[1:]:
        def at(i: int | None, fields: list[str] = fields) -> str | None:
            if i is None or i >= len(fields):
                return None
            return fields[i] or None

        rel = at(i_path)
        if not rel:
            continue
        out[rel] = _InheritedMeta(
            format_name=at(i_name), puid=at(i_puid), mime=at(i_mime),
            sha256=at(i_sha), virus_state=at(i_virus)
        )
    return out


def _parse_description_csv(text: str) -> DescriptiveMetadata | None:
    """AtoM 記述シート（26 列・1 行）から dmdSec 用の値を継承する。"""
    rows = _rows(text)
    if len(rows) < 2:
        return None

    header, values = rows[0], rows[1]

    def at(name: str) -> str | None:
        try:
            i = header.index(name)
        except ValueError:
            return None
        return (values[i] or None) if i < len(values) else None

    d = DescriptiveMetadata(
        identifier=at("Identifier"),
        title=at("Title"),
        creator=at("Archive Creator"),
        date=at("Date expression"),
        description=at("Scope and content"),
        extent=at("Extent and medium"),
        language=at("Language of material (optional)"),
        access_rights=at("Conditions governing access (optional)"),
    )
    return d if d.has_any else None


def _objects_relative(filename: str) -> str | None:
    """metadata.csv の filename を objects/ からの相対パスに直す。

    全体を指す行は空文字を返し、objects 配下でない行は None を返す。
    新旧 4 通りの書き方を受け付ける。

        "objects"         新（全体行）      "objects/"        旧（全体行）
        "objects/a.txt"   新旧（ファイル）  "data/objects/…"  bag（data/ が 1 段付く）
    """
    path = filename.strip().replace("\\", "/")
    if path.startswith("data/"):
        path = path[len("data/") :]
    if path.rstrip("/") == "objects":
        return ""
    if path.startswith("objects/"):
        return path[len("objects/") :]
    return None


def _parse_metadata_csv(text: str) -> dict[str, DescriptiveMetadata]:
    """Archivematica 風 metadata.csv から file 単位の記述メタデータを読む。

    filename の書き方は版によって違う（_objects_relative を参照）。
    全体を指す行は空文字のキーに入れ、呼び出し側が SIP 全体の記述として使う。
    """
    rows = _rows(text)
    if len(rows) < 2:
        return {}

    header = rows[0]

    def col(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    i_file = col("filename")
    if i_file is None:
        return {}

    mapping = {
        "title": col("dc.title"),
        "creator": col("dc.creator"),
        "date": col("dc.date"),
        "description": col("dc.description"),
        "extent": col("dcterms.extent"),
        "language": col("dc.language"),
        "access_rights": col("dcterms.accessRights"),
    }

    out: dict[str, DescriptiveMetadata] = {}
    for fields in rows[1:]:
        if i_file >= len(fields):
            continue
        rel = _objects_relative(fields[i_file])
        if rel is None:
            continue

        def at(key: str, fields: list[str] = fields) -> str | None:
            i = mapping[key]
            if i is None or i >= len(fields):
                return None
            return fields[i] or None

        d = DescriptiveMetadata(
            title=at("title"), creator=at("creator"), date=at("date"),
            description=at("description"), extent=at("extent"),
            language=at("language"), access_rights=at("access_rights"),
        )
        if d.has_any:
            out[rel] = d
    return out


def _parse_manifest(sip_root: Path, *, is_bag: bool) -> dict[str, str]:
    """マニフェストから objects 配下の 相対パス -> SHA-256 を読む。

    非 bag では **2 箇所を見る。** 0.1.x が作った SIP は
    `metadata/submissionDocumentation/checksum.sha256` に `objects/` 付きで、
    以降は `metadata/checksum.sha256` に `objects/` 抜きで書いている。
    片方しか見ないと、どちらかの世代の SIP でハッシュを継承できず、
    AIP 段で全ファイルを再計算することになる（受入時の記録との突合ができなくなる）。
    """
    if is_bag:
        candidates = [(sip_root / "manifest-sha256.txt", "data/objects/")]
    else:
        candidates = [
            # 旧 → 新の順。同じパスがあれば新しい方で上書きする。
            # 接頭辞は候補ごとに決め打ちする。「付いていれば外す」式にすると、
            # objects/ の中に objects という名前のフォルダがある SIP で誤読する。
            (sip_root / "metadata" / "submissionDocumentation" / "checksum.sha256", "objects/"),
            (sip_root / "metadata" / "checksum.sha256", ""),
        ]

    out: dict[str, str] = {}
    for manifest, prefix in candidates:
        if manifest.is_file():
            out.update(_manifest_entries(_read_text(manifest), prefix=prefix))
    return out


def _manifest_entries(text: str, *, prefix: str) -> dict[str, str]:
    """"<hash><空白>+<パス>" の並びを objects/ からの相対パス -> SHA-256 にする。

    prefix はそのマニフェストが使っているパスの基点（空なら objects/ 直下基準）。
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        parts = trimmed.split(None, 1)
        if len(parts) != 2:
            continue
        digest, path = parts[0], parts[1].lstrip("*")
        if not path.startswith(prefix):
            continue
        out[path[len(prefix) :]] = digest
    return out


def _first_existing_dir(root: Path, candidates) -> Path | None:
    for rel in candidates:
        candidate = root / rel
        if candidate.is_dir():
            return candidate
    return None


def _first_existing_file(candidates) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
