"""人間可読レポート（テキスト / HTML）。

退役した Swift 実装の `Sources/SIP/Orchestrator.swift` の `makeReportText` と
`HTMLReport.swift` に由来する。

このレポートは「担当者が目視で確認すべき点」を上に集める。未識別・拡張子不一致・
ウイルス検出・PII 候補は、機械的に処理を止める理由にはならないが、
人が見て判断すべき事柄なので埋もれさせない。
"""

from __future__ import annotations

import html
from datetime import UTC, datetime

from .models import ScannedFile, SIPMetadata, SIPOptions

# 一覧に載せる上限。何千件も並べても読めないので、件数だけ示して打ち切る。
_LIST_LIMIT = 100


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_counts(files: list[ScannedFile]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for f in files:
        counts[f.format_name or "（未識別）"] = counts.get(f.format_name or "（未識別）", 0) + 1
    # 件数降順、同数なら名前順。出力を決定的にする。
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _mismatched(files: list[ScannedFile]) -> list[ScannedFile]:
    return [f for f in files if f.format_warning and "mismatch" in f.format_warning.lower()]


def _nothing_to_review(*groups: list) -> str:
    """目視確認の項目が 1 つも無いときだけ、その旨を出す。"""
    return "" if any(groups) else "<p>目視確認が必要な点はありません。</p>"


def _pii_summary(files: list[ScannedFile], options: SIPOptions) -> str:
    hit_files = [f for f in files if f.pii]
    total = sum(len(f.pii) for f in hit_files)
    unreadable = sum(1 for f in files if f.pii_unreadable)
    if not options.scan_pii:
        return "未実施（オプション OFF）"

    # 「候補なし」とだけ書くと、読めなかったファイルの分まで
    # 安全だと受け取られる。走査できていない件数は必ず添える。
    tail = f"／うち {unreadable} ファイルは読み取れず走査できず" if unreadable else ""
    if total == 0:
        return f"実施（候補なし{tail}）"
    return f"実施（候補 {total} 件 / {len(hit_files)} ファイル{tail}）"


def _truncated(items: list[str]) -> tuple[list[str], int]:
    """一覧と、載せきれなかった件数を返す。"""
    if len(items) <= _LIST_LIMIT:
        return items, 0
    return items[:_LIST_LIMIT], len(items) - _LIST_LIMIT


def text_report(
    files: list[ScannedFile],
    metadata: SIPMetadata,
    options: SIPOptions,
    *,
    virus_status: str,
    format_status: str = "実施",
    source_archive_note: str = "",
) -> str:
    total_bytes = sum(f.size_bytes for f in files)
    unidentified = [f for f in files if f.puid is None]
    mismatched = _mismatched(files)
    infected = [f for f in files if f.virus]
    pii_files = [f for f in files if f.pii]

    lines: list[str] = []
    if source_archive_note:
        lines.append(source_archive_note.rstrip("\n"))
        lines.append("")

    lines += [
        "Archival Packager レポート",
        "==========================",
        "",
        f"生成日時: {_iso_now()}",
        f"識別子  : {metadata.identifier}",
        f"タイトル: {metadata.title}",
        f"年代    : {metadata.date_note}",
        f"梱包形式: {'BagIt bag' if options.make_bag else 'SIP ディレクトリ'}",
        "",
        f"ファイル数: {len(files)}",
        f"合計サイズ: {total_bytes} バイト",
        "",
        "処理サマリ:",
        f"  フォーマット識別: {format_status}",
        f"  ウイルスチェック: {virus_status}",
        f"  個人情報(PII)スキャン: {_pii_summary(files, options)}",
        "  受入記録: accession.csv（原パス↔SHA-256）",
        "",
    ]

    if infected:
        lines.append(f"ウイルス検出: {len(infected)} 件")
        shown, rest = _truncated([f"  - {f.relative_path}: {f.virus}" for f in infected])
        lines += shown
        if rest:
            lines.append(f"  （他 {rest} 件）")
        lines.append("")

    lines.append("フォーマット内訳:")
    lines += [f"  {count} 件\t{name}" for name, count in _format_counts(files)]
    lines.append("")

    for label, group in (("未識別", unidentified), ("拡張子不一致", mismatched)):
        lines.append(f"{label}: {len(group)} 件")
        shown, rest = _truncated([f"  - {f.relative_path}" for f in group])
        lines += shown
        if rest:
            lines.append(f"  （他 {rest} 件）")
        lines.append("")

    if pii_files:
        total = sum(len(f.pii) for f in pii_files)
        lines.append(
            f"個人情報(PII)候補: {total} 件（{len(pii_files)} ファイル）— 詳細は pii-report.csv"
        )
        rows = []
        for f in pii_files:
            kinds: dict[str, int] = {}
            for p in f.pii:
                kinds[p.kind] = kinds.get(p.kind, 0) + 1
            breakdown = ", ".join(
                f"{k}×{v}" for k, v in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))
            )
            rows.append(f"  - {f.relative_path}: {breakdown}")
        shown, rest = _truncated(rows)
        lines += shown
        if rest:
            lines.append(f"  （他 {rest} 件）")

    return "\n".join(lines) + "\n"


def html_report(
    files: list[ScannedFile],
    metadata: SIPMetadata,
    options: SIPOptions,
    *,
    virus_status: str,
    format_status: str = "実施",
) -> str:
    """ブラウザで開ける要約。

    値はすべてエスケープする。資料名に < を含むだけで表示が崩れると、
    レポートとして信用できなくなる。
    """
    e = html.escape

    total_bytes = sum(f.size_bytes for f in files)
    unidentified = [f for f in files if f.puid is None]
    mismatched = _mismatched(files)
    infected = [f for f in files if f.virus]
    pii_files = [f for f in files if f.pii]

    def review_section(title: str, items: list[str]) -> str:
        if not items:
            return ""
        shown, rest = _truncated(items)
        body = "".join(f"<li>{e(x)}</li>" for x in shown)
        if rest:
            body += f"<li>（他 {rest} 件）</li>"
        return f"<h2>{e(title)}（{len(items)} 件）</h2><ul>{body}</ul>"

    rows = "".join(
        f"<tr><td>{count}</td><td>{e(name)}</td></tr>" for name, count in _format_counts(files)
    )

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>Archival Packager レポート — {e(metadata.title)}</title>
<style>
 body {{ font-family: -apple-system, "Hiragino Sans", "Yu Gothic UI", sans-serif;
         line-height: 1.7; margin: 2rem auto; max-width: 60rem; padding: 0 1rem; }}
 table {{ border-collapse: collapse; }}
 td, th {{ border: 1px solid #ccc; padding: .3rem .6rem; text-align: left; }}
 dt {{ font-weight: 600; }}
 .review {{ background: #fff8e1; padding: .5rem 1rem; border-left: 4px solid #f0ad4e; }}
</style>
</head>
<body>
<h1>Archival Packager レポート</h1>
<dl>
  <dt>生成日時</dt><dd>{e(_iso_now())}</dd>
  <dt>識別子</dt><dd>{e(metadata.identifier)}</dd>
  <dt>タイトル</dt><dd>{e(metadata.title)}</dd>
  <dt>年代</dt><dd>{e(metadata.date_note)}</dd>
  <dt>梱包形式</dt><dd>{'BagIt bag' if options.make_bag else 'SIP ディレクトリ'}</dd>
  <dt>ファイル数</dt><dd>{len(files)}</dd>
  <dt>合計サイズ</dt><dd>{total_bytes} バイト</dd>
  <dt>フォーマット識別</dt><dd>{e(format_status)}</dd>
  <dt>ウイルスチェック</dt><dd>{e(virus_status)}</dd>
  <dt>個人情報(PII)スキャン</dt><dd>{e(_pii_summary(files, options))}</dd>
</dl>

<div class="review">
{review_section("ウイルス検出", [f"{f.relative_path}: {f.virus}" for f in infected])}
{review_section("未識別", [f.relative_path for f in unidentified])}
{review_section("拡張子不一致", [f.relative_path for f in mismatched])}
{review_section("個人情報(PII)候補", [f"{f.relative_path}: {len(f.pii)} 件" for f in pii_files])}
{_nothing_to_review(infected, unidentified, mismatched, pii_files)}
</div>

<h2>フォーマット内訳</h2>
<table><tr><th>件数</th><th>フォーマット</th></tr>{rows}</table>
</body>
</html>
"""
