"""スプレッドシート（CSV）生成。

退役した Swift 実装の `Sources/SIP/DescriptiveSpreadsheet.swift` に由来する。

本家 sipcreator に倣い「記述メタデータ」と「技術メタデータ」を分離する:

    description()       担当者が読み書きする記述シート（26列・SIP 全体で 1 行）
                        列見出しは人間向けの英語ラベル（変更不可。後述）
    atom_import()       AtoM の csv:import にそのまま渡す CSV（機械名の列・階層あり）
    formats()           ファイル単位の技術インベントリ
    accession()         受入記録（配列前の原パス ↔ SHA-256）
    pii_report()        PII 検出レポート（マスク済み）
    metadata_template() Archivematica 風 metadata.csv の記入用雛形

BOM は書き出し側（sip_builder）が付与する。改行は Excel 互換のため CRLF。

## 記述シートを 2 本に分けている理由（2026-09-12）

`description.csv` の列見出しは "Parent ID" のような**人間向けラベル**で、AtoM が
期待する機械名（`parentId`）とは 1 つも一致しない。そのまま AtoM に食わせると
**エラーにならないまま全列が捨てられる**（未知の列は警告のみで無視される）。

かといって `description.csv` の見出しを機械名に変えることはできない。これは
担当者が手で書き込むシートであり、`core/sip_reader.py` が過去に作った SIP を
この見出しで読み戻している。見出しを変えると、公開済みのアプリで作った
パッケージから AIP を作れなくなる。

そこで **人が書くシート（description.csv）と、機械に渡すシート
（atom-import.csv）を別ファイルに分けた。** 後者は `atom_import()` が作る。
中身は前者から機械的に導出するので、二重管理にはならない。

## CSV のエスケープは標準ライブラリに任せる

Swift 版は csvEscape を自前で持っていた。ここでは標準の csv モジュールを使う。
RFC 4180 の引用規則（区切り文字・引用符・改行を含む場合の囲み、内部の " の二重化）は
実装が持っているので、自前で漏らす余地が無い。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC

from .models import ScannedFile, SIPMetadata

CRLF = "\r\n"
#: AtoM に渡す CSV だけ LF で書く。AtoM の CSV import は
#: 「AtoM's CSV import will expect Unix-style line breaks (\n)」と明記しており、
#: CRLF は「unintended blank rows」の原因として名指しされている（CSV validation）。
#: Excel 互換（CRLF）はここでは優先しない。人が開くのは description.csv の方。
LF = "\n"

# 担当者が読み書きするシートの列見出し（変更不可）。
# **AtoM の機械名ではない。** 変えると sip_reader が過去の SIP を読めなくなる。
# AtoM へ渡す列名は ATOM_MACHINE_COLUMNS（下）で、atom_import() が対応づける。
ATOM_HEADERS: tuple[str, ...] = (
    "Parent ID", "Identifier", "Title", "Archive Creator",
    "Date expression", "Date start", "Date end", "Level of description",
    "Extent and medium", "Scope and content", "Arrangement (optional)",
    "Accession number",
    "Appraisal, destruction, and scheduling information (optional)",
    "Name access points (optional)", "Geographic access points (optional)",
    "Conditions governing access (optional)",
    "Conditions governing reproduction (optional)",
    "Language of material (optional)",
    "Physical characteristics & technical requirements affecting use (optional)",
    "Finding aids (optional)", "Related units of description (optional)",
    "Archival history (optional)",
    "Immediate source of acquisition or transfer (optional)",
    "Archivists' note (optional)", "General note (optional)",
    "Description status",
)

#: ATOM_HEADERS と**同じ順**の、AtoM 側の機械名。位置で対応づける。
#: 出典は AtoM 2.8 の ISAD(G) データ入力テンプレート（項目名 → 列名の対応表）と、
#: AtoM 同梱の例 CSV `lib/task/import/example/isad/`。
#: 対応の根拠は docs/interoperability.md の 2.2 にある。
ATOM_MACHINE_COLUMNS: tuple[str, ...] = (
    "parentId", "identifier", "title", "eventActors",
    "eventDates", "eventStartDates", "eventEndDates", "levelOfDescription",
    "extentAndMedium", "scopeAndContent", "arrangement",
    "accessionNumber",
    "appraisal",
    "nameAccessPoints", "placeAccessPoints",
    "accessConditions",
    "reproductionConditions",
    "language",
    "physicalCharacteristics",
    "findingAids", "relatedUnitsOfDescription",
    "archivalHistory",
    "acquisition",
    "archivistNote", "generalNote",
    "descriptionStatus",
)

#: atom-import.csv の列。先頭に legacyId を足す。
#: legacyId が無いと AtoM の CsvLegacyIdValidator が
#: 「Future CSV updates may not match these records」と警告し、**CSV を直して
#: 入れ直したときに既存レコードを更新せず新しいレコードを作る**。移管を
#: 繰り返す運用では同じ資料が何件も並ぶことになるので、必ず出す。
ATOM_IMPORT_HEADERS: tuple[str, ...] = ("legacyId", *ATOM_MACHINE_COLUMNS)

#: SIP 全体の行に付ける記述階層。ファイル 1 件ずつはその下に Item として並べる。
#: どちらも AtoM の levelOfDescription タクソノミーに実在する語。
ATOM_LEVEL_WHOLE = "File"
ATOM_LEVEL_ITEM = "Item"

METADATA_TEMPLATE_HEADERS: tuple[str, ...] = (
    "filename", "dc.title", "dc.creator", "dc.date",
    "dc.description", "dcterms.extent", "dc.language", "dcterms.accessRights",
)


def _to_csv(rows: list[list[str]], *, terminator: str = CRLF) -> str:
    buf = io.StringIO()
    # lineterminator を明示する。既定は \r\n だが、依存したくないので固定する。
    writer = csv.writer(buf, lineterminator=terminator, quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    return buf.getvalue()


def _iso(dt) -> str:
    """ISO8601（UTC・秒精度）。Swift の ISO8601DateFormatter の既定出力に合わせる。"""
    # tz を持たない値は UTC とみなす。dfxml._iso と揃える（両者がずれると、
    # 同じファイルの更新日時が技術メタデータと記述シートで食い違う）。
    # Windows では、素の datetime を astimezone でローカル時刻に変換しようとすると
    # 1970-01-01 前後で OSError になる、という事情もある。
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def description(files: list[ScannedFile], metadata: SIPMetadata) -> str:
    """記述シート（26 列・1 行）。担当者が手で書き足す前提の人間向けシート。"""
    return _to_csv([list(ATOM_HEADERS), _whole_values(files, metadata)])


def _whole_values(files: list[ScannedFile], metadata: SIPMetadata) -> list[str]:
    """SIP 全体を表す 1 行（ATOM_HEADERS の並び）。

    description.csv と atom-import.csv で同じ値を使うため、ここに 1 箇所だけ置く。
    別々に組み立てると、片方だけ直したときに 2 つのシートの内容がずれる。
    """
    start_date, end_date = _date_range(files)
    values = [""] * len(ATOM_HEADERS)
    values[1] = metadata.identifier or metadata.title
    values[2] = metadata.title
    values[4] = metadata.date_note
    values[5] = start_date
    values[6] = end_date
    values[7] = ATOM_LEVEL_WHOLE
    values[8] = f"{len(files)} digital files ({_byte_string(files)})"
    values[9] = metadata.scope_note or _format_summary(files)
    return values


def atom_import(files: list[ScannedFile], metadata: SIPMetadata) -> str:
    """AtoM の csv:import にそのまま渡せる CSV（機械名の列・階層つき）。

    description.csv との違いは 3 つで、いずれも「人が読む」ためではなく
    「AtoM が読む」ために要るもの。

    1. **列名が機械名。** 人間向けラベルのままだと未知の列として黙って捨てられる。
    2. **legacyId を出す。** 無いと再インポートで既存レコードに紐づかず重複する。
    3. **階層を出す。** 1 行目が SIP 全体、2 行目以降がファイル 1 件ずつで、
       各行の parentId が全体行の legacyId を指す。こうしておけば、AtoM 側で
       ファイル単位の目録を作り直さずに済む。

    行の順序には意味がある。AtoM は上から 1 行ずつ取り込むので、
    **親の行が子より上に無いとインポートが失敗する**（csv-import の Important 注記）。
    """
    whole = _whole_values(files, metadata)
    whole_id = whole[1] or metadata.title

    rows: list[list[str]] = [list(ATOM_IMPORT_HEADERS), [whole_id, *whole]]
    for f in files:
        values = [""] * len(ATOM_HEADERS)
        values[0] = whole_id  # parentId — 全体行の legacyId を指す
        values[1] = f.relative_path
        values[2] = f.relative_path.rsplit("/", 1)[-1]
        date = f.modified.strftime("%Y-%m-%d")
        values[4] = date
        values[5] = date
        values[6] = date
        values[7] = ATOM_LEVEL_ITEM
        values[8] = f"1 digital file ({_byte_string([f])})"
        values[18] = _technical_note(f)
        # legacyId は「この SIP の中でこのファイル」を一意に指せばよい。
        # 相対パスを使うと、同じ資料を作り直したときも同じ値になり、
        # 再インポートで既存レコードを更新できる（重複しない）。
        rows.append([f"{whole_id}/{f.relative_path}", *values])

    return _to_csv(rows, terminator=LF)


def _technical_note(f: ScannedFile) -> str:
    """ISAD(G) 3.4.4「物理的特徴と技術的条件」に入れる、利用に要る技術情報。

    フォーマットが分からないと将来その資料を開けるかどうかを判断できない。
    PRONOM ID は人間には読めないが、AtoM 上で機械的に拾えるように残す。
    """
    parts = [p for p in (f.format_name, f.puid) if p]
    return " / ".join(parts)


def accession(files: list[ScannedFile]) -> str:
    """受入記録。ファイル単位で「受入時点の原パス ↔ SHA-256」を束ねたスナップショット。

    配列（フォルダ並べ替え）の前に走らせると、ここに記録される原パスが
    「配列前の構造」になる。配列後の SIP とは SHA-256 で突合できる。

    同一性アンカーは内容ハッシュのみ。原本は一切変更しない。
    内容が完全に同一のファイルは原パスの対応が一意に定まらないが、
    内容が等価なため保存・完全性の観点で実害は小さい。
    """
    rows: list[list[str]] = [["原パス（受入時）", "SHA-256", "サイズ(バイト)", "更新日時"]]
    for f in files:
        rows.append(
            [
                # sanitize した場合は受入時の元パスを記録する
                # （SIP 内の sanitize 後名とは SHA-256 で突合できる）。
                f.original_relative_path or f.relative_path,
                f.sha256 or "",
                str(f.size_bytes),
                _iso(f.modified),
            ]
        )
    return _to_csv(rows)


def pii_report(files: list[ScannedFile]) -> str | None:
    """PII レポート（検出があった行のみ）。

    値は必ずマスク済み。レポート自体が漏洩源になってはならない。
    検出ゼロなら None を返し、ファイル自体を作らない
    （空ファイルがあると「検出あり」と誤解される）。
    """
    rows: list[list[str]] = []
    for f in files:
        for p in f.pii:
            rows.append([f.relative_path, p.kind, p.masked, str(p.line if p.line is not None else "")])

    if not rows:
        return None
    return _to_csv([["相対パス", "種別", "検出値（マスク）", "行"], *rows])


def formats(files: list[ScannedFile]) -> str:
    """技術インベントリ（ファイル単位）。brunnhilde の formats.csv 相当。"""
    rows: list[list[str]] = [
        # **ウイルス検査の結果は機械で読める形で残す。** レポートの文章の中だけだと、
        # AIP を作るときに「検査したか」を辿れず、PREMIS に記録できない。
        ["相対パス", "フォーマット名", "PRONOM", "MIME",
         "拡張子警告", "サイズ(バイト)", "更新日時", "SHA-256", "ウイルス検査"]
    ]
    for f in files:
        rows.append(
            [
                f.relative_path,
                f.format_name or "",
                f.puid or "",
                f.mime_type or "",
                f.format_warning or "",
                str(f.size_bytes),
                _iso(f.modified),
                f.sha256 or "",
                _virus_state(f),
            ]
        )
    return _to_csv(rows)


#: ウイルス検査の状態。「検査していない」と「検査して検出なし」を区別する。
#: 前者を安全と読み違えられては困る。
VIRUS_NOT_SCANNED = "未実施"
VIRUS_CLEAN = "検出なし"


def _virus_state(f: ScannedFile) -> str:
    if f.virus:
        return f"検出: {f.virus}"
    return VIRUS_CLEAN if f.scanned_for_virus else VIRUS_NOT_SCANNED


#: metadata.csv の filename 列の基点。bag かどうかで変わる。
#:
#: Archivematica の import-metadata は、非 bag では
#: 「the filename path must always start with ``objects/``」、bag では
#: 「Material that you add to a bag will always be inside the payload directory,
#: so the filename path must always begin with ``data``」と定めている。
#: **同じ文字列を両方に書くと、bag のとき転送内に該当する実体が無く、
#: ファイル単位の記述メタデータが 1 件も紐づかない。**
OBJECTS_BASE = "objects"
BAG_OBJECTS_BASE = "data/objects"


def metadata_base(*, bagged: bool) -> str:
    return BAG_OBJECTS_BASE if bagged else OBJECTS_BASE


def metadata_template(files: list[ScannedFile], *, bagged: bool = False) -> str:
    """Archivematica 風 metadata.csv の雛形（記入用）。

    `filename` を `<基点>/<相対パス>` で埋め、Dublin Core 列は空欄にする。
    担当者がここに記述を書き込むと、AIP 作成時に file 単位の dmdSec として反映される。

    先頭に基点そのものの行（`objects`）を置き、全体の記述も書けるようにする。
    **末尾スラッシュを付けない。** Archivematica の例はファイル
    （`objects/beihai.tif`）かディレクトリ（`objects/CoastNews-1964-01-02`）で、
    `objects/` という書き方は無い。転送内にその名前の実体が存在しないため、
    対応先が見つからず行ごと落ちる可能性がある。
    """
    base = metadata_base(bagged=bagged)
    blanks = [""] * (len(METADATA_TEMPLATE_HEADERS) - 1)
    rows: list[list[str]] = [list(METADATA_TEMPLATE_HEADERS), [base, *blanks]]
    for f in files:
        rows.append([f"{base}/{f.relative_path}", *blanks])
    return _to_csv(rows)


def rebase_metadata_csv(text: str, *, bagged: bool) -> str:
    """既に記入済みの metadata.csv の filename 列だけを、bag / 非 bag に合わせ直す。

    構造化入力（objects/ を持つフォルダ）を受け取ったとき、担当者が書いた
    metadata.csv はそのまま引き継ぐ。ただし **bag 化する場合、filename が
    `objects/…` のままだと Archivematica から見て実体の無いパスになる**ので、
    そこだけ `data/` を足す（逆もある）。記述の中身には触らない。

    書いた人の内容を上書きしないことと、Archivematica が読めることは
    両立させる必要がある。片方だけ立てると、どちらかが黙って壊れる。
    """
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"), newline="")))
    if not rows:
        return text
    try:
        i_file = rows[0].index("filename")
    except ValueError:
        # filename 列が無いものは Archivematica 用の metadata.csv ではない。触らない。
        return text

    base = metadata_base(bagged=bagged)
    other = metadata_base(bagged=not bagged)
    for fields in rows[1:]:
        if i_file >= len(fields):
            continue
        value = fields[i_file]
        # 旧版のアプリが書いた "objects/"（末尾スラッシュだけの全体行）もここで直す。
        if value.rstrip("/") in (base, other):
            fields[i_file] = base
        elif value.startswith(other + "/"):
            fields[i_file] = base + value[len(other) :]
    return _to_csv(rows)


# --------------------------------------------------------------------------
# 補助
# --------------------------------------------------------------------------


def _date_range(files: list[ScannedFile]) -> tuple[str, str]:
    """ファイル更新日時の最小・最大（YYYY-MM-DD）。ファイルが無ければ空。"""
    if not files:
        return "", ""
    dates = [f.modified for f in files]
    return min(dates).strftime("%Y-%m-%d"), max(dates).strftime("%Y-%m-%d")


def _byte_string(files: list[ScannedFile]) -> str:
    """人間可読なバイト数。

    Swift 版は ByteCountFormatter(.file) を使っており、1000 進で "1.2 MB" のように出す。
    ただし 1000 未満のときの単位はロケール依存で、日本語環境では "99 バイト" になる
    （"99 bytes" ではない）。差分検証でここが唯一の不一致として出たので合わせた。
    接頭辞付きの単位（kB/MB/...）はロケールに依らずラテン文字のまま。
    """
    total = sum(f.size_bytes for f in files)
    if total < 1000:
        return f"{total} バイト"
    value = float(total)
    for unit in ("kB", "MB", "GB", "TB", "PB"):
        value /= 1000
        if value < 1000:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} EB"


def _format_summary(files: list[ScannedFile]) -> str:
    """scope 未入力時のフォールバック（本家の "Most common file formats: ..." に倣う）。"""
    counts: dict[str, int] = {}
    for f in files:
        key = f.format_name or "Unidentified"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return ""
    # 件数降順。同数のときは名前順にして出力を決定的にする
    # （dict の挿入順に任せると入力の走査順で結果が変わる）。
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    return "Most common file formats: " + ", ".join(name for name, _ in top)
