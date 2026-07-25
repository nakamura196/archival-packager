"""スプレッドシート（CSV）生成。

現行 Swift 実装の `Sources/SIP/DescriptiveSpreadsheet.swift` に対応する。

本家 sipcreator に倣い「記述メタデータ」と「技術メタデータ」を分離する:

    description()       AtoM/ISAD(G) インポート用の記述シート（26列・SIP 全体で 1 行）
                        列見出しは AtoM が期待する英語の固定文字列（変更不可）
    formats()           ファイル単位の技術インベントリ
    accession()         受入記録（配列前の原パス ↔ SHA-256）
    pii_report()        PII 検出レポート（マスク済み）
    metadata_template() Archivematica 風 metadata.csv の記入用雛形

BOM は書き出し側（sip_builder）が付与する。改行は Excel 互換のため CRLF。

## CSV のエスケープは標準ライブラリに任せる

Swift 版は csvEscape を自前で持っていた。ここでは標準の csv モジュールを使う。
RFC 4180 の引用規則（区切り文字・引用符・改行を含む場合の囲み、内部の " の二重化）は
実装が持っているので、自前で漏らす余地が無い。
"""

from __future__ import annotations

import csv
import io
from datetime import timezone

from .models import ScannedFile, SIPMetadata

CRLF = "\r\n"

# 本家準拠の列見出し（AtoM インポートが認識する英語固定文字列。変更不可）。
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

METADATA_TEMPLATE_HEADERS: tuple[str, ...] = (
    "filename", "dc.title", "dc.creator", "dc.date",
    "dc.description", "dcterms.extent", "dc.language", "dcterms.accessRights",
)


def _to_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    # lineterminator を明示する。既定は \r\n だが、依存したくないので固定する。
    writer = csv.writer(buf, lineterminator=CRLF, quoting=csv.QUOTE_MINIMAL)
    writer.writerows(rows)
    return buf.getvalue()


def _iso(dt) -> str:
    """ISO8601（UTC・秒精度）。Swift の ISO8601DateFormatter の既定出力に合わせる。"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def description(files: list[ScannedFile], metadata: SIPMetadata) -> str:
    """AtoM/ISAD(G) 記述シート（26 列・1 行）。"""
    start_date, end_date = _date_range(files)
    extent = f"{len(files)} digital files ({_byte_string(files)})"
    scope = metadata.scope_note or _format_summary(files)
    identifier = metadata.identifier or metadata.title

    values = [""] * len(ATOM_HEADERS)
    values[1] = identifier
    values[2] = metadata.title
    values[4] = metadata.date_note
    values[5] = start_date
    values[6] = end_date
    values[7] = "File"
    values[8] = extent
    values[9] = scope

    return _to_csv([list(ATOM_HEADERS), values])


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
        ["相対パス", "フォーマット名", "PRONOM", "MIME",
         "拡張子警告", "サイズ(バイト)", "更新日時", "SHA-256"]
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
            ]
        )
    return _to_csv(rows)


def metadata_template(files: list[ScannedFile]) -> str:
    """Archivematica 風 metadata.csv の雛形（記入用）。

    `filename` を `objects/<相対パス>` で埋め、Dublin Core 列は空欄にする。
    担当者がここに記述を書き込むと、AIP 作成時に file 単位の dmdSec として反映される。
    先頭に `objects/`（SIP 全体行）を置き、全体の記述も書けるようにする。
    """
    blanks = [""] * (len(METADATA_TEMPLATE_HEADERS) - 1)
    rows: list[list[str]] = [list(METADATA_TEMPLATE_HEADERS), ["objects/", *blanks]]
    for f in files:
        rows.append([f"objects/{f.relative_path}", *blanks])
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

    Swift 版は ByteCountFormatter(.file) を使っており、これは 1000 進で
    "1.2 MB" のように出す。同じ体裁に揃える。
    """
    total = sum(f.size_bytes for f in files)
    if total < 1000:
        return f"{total} bytes"
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
