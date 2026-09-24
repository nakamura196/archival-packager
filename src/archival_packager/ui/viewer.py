"""生成結果のビューア。

**ファイルを開くのは Finder / エクスプローラーに任せる。**
ここが見せるのは、それらでは見えないもの＝ METS と PREMIS の中身。
原本は PDF や Word や画像なので、どのみちアプリの中では開けない。
ファイルを並べるだけなら OS の方が便利で、それと張り合っても意味がない。

  概要     何がいくつ入っているか
  処理の記録  いつ・何を・どのツールで行い、結果はどうだったか（PREMIS）
  ワークフロー 同じ記録を段階ごとに束ねた流れ図。段階を押すと中身が出る
  ファイル   フォーマット・PRONOM・サイズ・SHA-256・ウイルス検査
  生データ   XML や CSV をそのまま読みたいとき（従来の表示）

読み取りは core/package_report.py、色分けは core/preview.py にある。
ここは並べ方だけを持つ。

大仙市アーカイブズでの聞き取りでは、生成された情報パッケージの中身を
画面上で見せたことが理解を助けた。パスを出すだけでは、何ができたのかが
伝わらない。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import flet as ft
import flet.canvas as cv

from ..core import package_report, preview
from ..i18n import raw, t
from . import messages
from . import platform as plat

#: 役割ごとの色。配色は表示の領分なので、ここで決める。
_COLORS = {
    preview.TEXT: None,
    preview.MARKUP: ft.Colors.ON_SURFACE_VARIANT,
    preview.ELEMENT: ft.Colors.PURPLE_700,
    preview.ATTRIBUTE: ft.Colors.TEAL_700,
    preview.VALUE: ft.Colors.ORANGE_800,
    preview.COMMENT: ft.Colors.GREY_600,
}

#: 拡張子ごとのアイコン。何のファイルかを名前を読まずに掴めるようにする。
_ICONS = {
    ".xml": ft.Icons.CODE,
    ".csv": ft.Icons.TABLE_CHART_OUTLINED,
    ".txt": ft.Icons.DESCRIPTION_OUTLINED,
    ".html": ft.Icons.HTML,
    ".pdf": ft.Icons.PICTURE_AS_PDF_OUTLINED,
    ".tif": ft.Icons.IMAGE_OUTLINED,
    ".tiff": ft.Icons.IMAGE_OUTLINED,
    ".png": ft.Icons.IMAGE_OUTLINED,
    ".jpg": ft.Icons.IMAGE_OUTLINED,
}

_MONO = "Menlo, Consolas, monospace"


def _icon_for(name: str) -> str:
    return _ICONS.get(Path(name).suffix.lower(), ft.Icons.INSERT_DRIVE_FILE_OUTLINED)


def _human_size(n: int) -> str:
    # 1000 未満の単位だけが言語で変わる（kB 以上はラテン文字のまま）。
    small = t("バイト")
    for unit in (small, "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == small else f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n} {small}"


def _raw_browser(root: Path) -> ft.Control:
    """従来の表示（左にツリー、右に中身）。XML や CSV を直接読みたいとき用。"""
    body = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=0)
    tree = ft.ListView(expand=True, spacing=0, padding=6)
    selected: dict[str, Path | None] = {"path": None}

    def show(path: Path) -> None:
        selected["path"] = path
        body.controls.clear()
        try:
            content = preview.read(path)
        except OSError as exc:
            body.controls.append(
                ft.Text(t("読み取れませんでした: {error}", error=exc), size=12)
            )
            body.update()
            return

        if isinstance(content, preview.BinaryPreview):
            body.controls.append(
                ft.Column(
                    [
                        ft.Text(path.name, weight=ft.FontWeight.BOLD),
                        ft.Text(t("テキストとして表示できない形式です"), size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(t("サイズ: {size}",
                                  size=_human_size(content.size_bytes)), size=12),
                        ft.Text(t("先頭バイト: {head}", head=content.head_hex), size=11,
                                font_family=_MONO,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    spacing=6,
                )
            )
        else:
            if path.suffix.lower() == ".xml":
                spans = [
                    ft.TextSpan(text, ft.TextStyle(color=_COLORS.get(role)))
                    for text, role in preview.highlight_xml(content.text)
                ]
                shown: ft.Control = ft.Text(
                    spans=spans, size=11, font_family=_MONO, selectable=True
                )
            else:
                shown = ft.Text(
                    content.text, size=11, font_family=_MONO, selectable=True
                )
            items: list[ft.Control] = [shown]
            if content.truncated:
                items.append(
                    ft.Text(
                        t("※ 先頭 {size} のみ表示しています。"
                          "全体はファイルを直接お開きください。",
                          size=_human_size(preview.MAX_BYTES)),
                        size=11, color=ft.Colors.ON_SURFACE_VARIANT,
                    )
                )
            body.controls.append(ft.Column(items, spacing=8))
        body.update()

    for entry in preview.walk(root):
        if entry.is_dir:
            row = ft.Row(
                [
                    ft.Icon(ft.Icons.FOLDER, size=14, color=ft.Colors.AMBER_700),
                    ft.Text(entry.name, size=12, weight=ft.FontWeight.W_500),
                ],
                spacing=6,
            )
            tree.controls.append(
                ft.Container(row, padding=ft.Padding.only(
                    left=6 + entry.depth * 14, top=2, bottom=2))
            )
        else:
            path = entry.path

            def _on_click(_e: ft.ControlEvent, p: Path = path) -> None:
                show(p)

            row = ft.Row(
                [
                    ft.Icon(_icon_for(entry.name), size=14,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Text(entry.name, size=12, font_family=_MONO),
                ],
                spacing=6,
            )
            tree.controls.append(
                ft.Container(
                    row,
                    padding=ft.Padding.only(
                        left=6 + entry.depth * 14, top=2, bottom=2),
                    on_click=_on_click,
                    ink=True,
                    border_radius=4,
                )
            )

    body.controls.append(
        ft.Column(
            [
                ft.Icon(ft.Icons.FIND_IN_PAGE_OUTLINED, size=36,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Text(t("左のファイルを選ぶと内容を表示します"), size=12,
                        color=ft.Colors.ON_SURFACE_VARIANT),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
    )

    return ft.Row(
        [
            ft.Container(tree, width=340),
            ft.VerticalDivider(width=1),
            ft.Container(body, expand=True, padding=10),
        ],
        expand=True,
        spacing=0,
    )


# --------------------------------------------------------------------------
# METS / PREMIS を読んで見せる側
# --------------------------------------------------------------------------

_LABEL_COLOR = ft.Colors.ON_SURFACE_VARIANT


def _pair(label: str, value: str) -> ft.Control:
    return ft.Row(
        [
            ft.Container(ft.Text(label, size=12, color=_LABEL_COLOR), width=120),
            ft.Text(value or t("（未記入）"), size=12, selectable=True),
        ],
        spacing=8,
    )


def _table(columns: list[tuple[str, int]], rows: list[list[ft.Control]]) -> ft.Control:
    """横に長くなるので、表そのものを横スクロールさせる。

    **縦にも流す。** 横スクロールの Row だけで包むと、表は上下の中央に置かれ
    （件数が少ないと見出しの下に大きな空白ができた）、件数が多いと
    画面の下で切れたまま、下の行へ進めなかった。
    """
    table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(name, size=12, weight=ft.FontWeight.BOLD))
                 for name, _w in columns],
        rows=[ft.DataRow(cells=[ft.DataCell(c) for c in cells]) for cells in rows],
        heading_row_height=36,
        data_row_min_height=32,
        data_row_max_height=48,
        column_spacing=18,
    )
    return ft.Column(
        [ft.Row([table], scroll=ft.ScrollMode.AUTO)],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )


def _mono(text: str, size: int = 11) -> ft.Control:
    return ft.Text(text, size=size, font_family=_MONO, selectable=True)


#: 円グラフの色。多い順に割り当てる。7 種を超えた分は「その他」にまとめる。
_SLICE_COLORS = (
    ft.Colors.BLUE_400, ft.Colors.TEAL_400, ft.Colors.ORANGE_400,
    ft.Colors.PURPLE_300, ft.Colors.GREEN_400, ft.Colors.RED_300,
    ft.Colors.BROWN_300,
)
_OTHER_COLOR = ft.Colors.BLUE_GREY_300
_MAX_SLICES = 7


def _slices(formats: list[tuple[str, int]]) -> list[tuple[str, int, str]]:
    """(名前, 件数, 色) の並びにする。多すぎる分はまとめる。"""
    head = formats[:_MAX_SLICES]
    rest = formats[_MAX_SLICES:]
    out = [(name, n, _SLICE_COLORS[i % len(_SLICE_COLORS)])
           for i, (name, n) in enumerate(head)]
    if rest:
        out.append((t("その他 {count} 種", count=len(rest)),
                    sum(n for _name, n in rest), _OTHER_COLOR))
    return out


def _pie(formats: list[tuple[str, int]], size: int = 160) -> ft.Control:
    """フォーマットごとの割合を円で見せる。

    Flet 0.86 にグラフの部品は無いので、canvas の扇形を並べて描く。
    中央を抜いてドーナツにし、真ん中に総数を出す。
    """
    import math

    total = sum(n for _name, n, _c in _slices(formats)) or 1
    shapes: list[cv.Shape] = []
    start = -math.pi / 2  # 12 時から時計回り
    for _name, count, color in _slices(formats):
        sweep = 2 * math.pi * count / total
        shapes.append(
            cv.Arc(0, 0, size, size, start, sweep, use_center=True,
                   paint=ft.Paint(color=color))
        )
        start += sweep
    # 中抜き。背景と同じ色で塗る。
    hole = size * 0.52
    offset = (size - hole) / 2
    shapes.append(
        cv.Arc(offset, offset, hole, hole, 0, 2 * math.pi, use_center=True,
               paint=ft.Paint(color=ft.Colors.SURFACE))
    )
    return ft.Stack(
        [
            cv.Canvas(shapes, width=size, height=size),
            ft.Container(
                ft.Column(
                    [
                        ft.Text(str(total), size=20, weight=ft.FontWeight.BOLD),
                        ft.Text(t("原本"), size=11, color=_LABEL_COLOR),
                    ],
                    spacing=0,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                width=size, height=size, alignment=ft.Alignment.CENTER,
            ),
        ],
        width=size, height=size,
    )


def _legend(formats: list[tuple[str, int]]) -> ft.Control:
    rows: list[ft.Control] = []
    for name, count, color in _slices(formats):
        rows.append(
            ft.Row(
                [
                    ft.Container(width=10, height=10, bgcolor=color, border_radius=2),
                    ft.Text(name, size=12, expand=True),
                    ft.Text(t("{count} 件", count=count), size=12, color=_LABEL_COLOR),
                ],
                spacing=8,
            )
        )
    return ft.Column(rows, spacing=6, tight=True)


def _stat(label: str, value: str, *, warn: bool = False) -> ft.Control:
    return ft.Container(
        ft.Column(
            [
                ft.Text(value, size=18, weight=ft.FontWeight.BOLD,
                        color=ft.Colors.ORANGE_800 if warn else None),
                ft.Text(label, size=11, color=_LABEL_COLOR),
            ],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=18, vertical=10),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=8,
    )


def _breakdown(report: package_report.PackageReport) -> ft.Control:
    """全体の内訳。1 件ずつの表を見なくても、何が入っていて
    どれだけ手が入ったかが分かるようにする。"""
    summary = report.summary
    if not summary.formats:
        return ft.Container()

    stats = [
        _stat(t("保存用に変換"), t("{count} 件", count=summary.normalized)),
        _stat(t("ウイルス検査済"), t("{count} 件", count=summary.virus_scanned)),
    ]
    if summary.unidentified:
        stats.append(
            _stat(t("未識別"), t("{count} 件", count=summary.unidentified), warn=True)
        )
    if summary.extension_warnings:
        stats.append(
            _stat(t("拡張子が不一致"),
                  t("{count} 件", count=summary.extension_warnings), warn=True)
        )

    return ft.Column(
        [
            ft.Text(t("内訳"), size=13, weight=ft.FontWeight.BOLD),
            ft.Row(
                [
                    _pie(summary.formats),
                    ft.Container(_legend(summary.formats), expand=True,
                                 padding=ft.Padding.only(left=8)),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row(stats, spacing=10, wrap=True),
        ],
        spacing=12,
    )


def _overview_tab(report: package_report.PackageReport) -> ft.Control:
    o = report.overview
    items: list[ft.Control] = [
        ft.Text(o.title or report.root.name, size=16, weight=ft.FontWeight.BOLD),
        ft.Text(
            {"AIP": t("保存用情報パッケージ（AIP）"),
             "SIP": t("提出用情報パッケージ（SIP）")}.get(o.kind, o.kind),
            size=12, color=_LABEL_COLOR,
        ),
        ft.Divider(height=16),
        _pair(t("識別子"), o.identifier),
        _pair(t("作成日時"), o.created),
        _pair(
            t("ファイル数"),
            t("原本 {count} 件", count=o.original_count)
            + (t("（ほかに {count} 件）", count=o.file_count - o.original_count)
               if o.file_count > o.original_count else ""),
        ),
        _pair(t("合計サイズ"), package_report.human_bytes(o.total_bytes)),
        _pair(t("置き場所"), str(report.root)),
    ]
    if report.mets_path is not None:
        items.append(_pair("METS", report.mets_path.name))
    if o.note:
        items.append(
            ft.Container(
                ft.Text(o.note, size=12, color=ft.Colors.ERROR),
                padding=ft.Padding.only(top=12),
            )
        )
    items.append(ft.Container(_breakdown(report), padding=ft.Padding.only(top=20)))
    items.append(
        ft.Container(
            ft.Row(
                [
                    ft.OutlinedButton(
                        t("フォルダを開く"),
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=lambda _e: plat.reveal_in_file_manager(report.root),
                    ),
                ],
                spacing=8,
            ),
            padding=ft.Padding.only(top=20),
        )
    )
    return ft.Container(
        ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO),
        padding=20,
        expand=True,
    )


def _events_tab(report: package_report.PackageReport) -> ft.Control:
    if not report.events:
        return _empty(
            t("処理の記録がありません"),
            t("SIP の段階では、処理の記録はまだありません。"
              "この SIP から AIP を作ると、行った処理がここに並びます。"),
        )

    rows = [
        [
            _mono(e.date_time),
            ft.Text(_stage_label(e.event_type), size=12, weight=ft.FontWeight.W_500),
            ft.Text(e.outcome, size=12),
            ft.Text(t("、").join(e.agents), size=12, color=_LABEL_COLOR),
            _mono(e.target or t("パッケージ全体")),
            ft.Text(messages.event_detail(e.detail), size=11, color=_LABEL_COLOR),
        ]
        for e in report.events
    ]
    # summary.events は日本語の表示名で数えてあるので、画面の言語に合わせて数え直す。
    counts: dict[str, int] = {}
    for e in report.events:
        label = _stage_label(e.event_type)
        counts[label] = counts.get(label, 0) + 1
    kinds = t("、").join(
        f"{name} {n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    head = ft.Container(
        ft.Column(
            [
                ft.Text(
                    t("{count} 件の記録。担当者が別に作業記録を書く必要はありません。",
                      count=len(report.events)),
                    size=12, color=_LABEL_COLOR,
                ),
                ft.Text(kinds, size=11, color=_LABEL_COLOR),
            ],
            spacing=2,
        ),
        padding=ft.Padding.only(left=16, top=12, bottom=4),
    )
    return ft.Column(
        [head, _table(
            [(t("日時"), 160), (t("処理"), 120), (t("結果"), 80), (t("実行したもの"), 140),
             (t("対象"), 200), (t("詳細"), 240)],
            rows,
        )],
        expand=True,
        spacing=0,
    )


def _stage_label(event_type: str) -> str:
    """段階の表示名。t() は呼ぶたびに今の言語を引くので、辞書は毎回作る。

    PREMIS の eventType そのもの（英語）は記録の語彙なので core では訳さない。
    ここで画面の言語に合わせる。知らない種類は eventType をそのまま出す。
    """
    return {
        "ingestion": t("取り込み"),
        "virus check": t("ウイルス検査"),
        "format identification": t("フォーマットの識別"),
        "normalization": t("保存用形式への変換"),
        "validation": t("変換結果の検証"),
        "message digest calculation": t("チェックサムの算出"),
        "fixity check": t("完全性の確認"),
    }.get(event_type, event_type)


def _stage_hint(event_type: str) -> str:
    """その段階で何を確かめているか。流れ図だけでは中身が伝わらないので添える。"""
    return {
        "ingestion": t("SIP の原本を AIP に取り込んだ記録です。"),
        "virus check": t("ClamAV で検査した記録です。"
                         "検査しなかったときは記録を書きません。"),
        "format identification": t("Siegfried で PRONOM の形式を特定した記録です。"),
        "normalization": t("長期保存に向く形式へ変換した記録です。"
                           "対象の形式だけが変換されます。"),
        "validation": t("変換で作ったファイルを開き直せたかの確認です。"
                        "原本の形式適合性の検査（JHOVE など）ではありません。"),
        "message digest calculation": t(
            "SHA-256 は各ファイルの記録（PREMIS object）に入っています。"
            "算出そのものは処理の記録としては書いていません。"),
        "fixity check": t("SIP の BagIt マニフェストとチェックサムを照合した記録です。"),
    }.get(event_type, "")


def _stage_status(stage: package_report.Stage) -> tuple[str, str]:
    """(アイコン, 色)。問題あり＞記録なし＞問題なし の順に強く見せる。"""
    if stage.problems:
        return ft.Icons.WARNING_AMBER_ROUNDED, ft.Colors.ORANGE_700
    if not stage.recorded:
        return ft.Icons.REMOVE_CIRCLE_OUTLINE, ft.Colors.GREY_500
    return ft.Icons.CHECK_CIRCLE_OUTLINE, ft.Colors.GREEN_700


def _stage_detail(stage: package_report.Stage) -> ft.Control:
    items: list[ft.Control] = [
        ft.Text(_stage_label(stage.event_type), size=15, weight=ft.FontWeight.BOLD),
        _mono(stage.event_type),
    ]
    hint = _stage_hint(stage.event_type)
    if hint:
        items.append(ft.Text(hint, size=12, color=_LABEL_COLOR))
    items.append(ft.Divider(height=12))

    if not stage.recorded:
        if stage.digests:
            note = t("SHA-256 が記録されたファイル: {count} 件", count=stage.digests)
        else:
            note = t("この段階の記録はありません。行わなかったか、記録されていません。")
        items.append(ft.Text(note, size=12))
        return ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    items += [
        _pair(t("記録"), str(stage.events)),
        _pair(t("対象ファイル"), t("{count} 件", count=stage.files)),
        # 結果の値（pass / fail …）は PREMIS の語彙なので訳さない。
        _pair(t("結果"), t("、").join(f"{name} {n}" for name, n in stage.outcomes)),
        _pair(t("実行したもの"), t("、").join(stage.agents)),
    ]

    if stage.problems:
        items.append(
            ft.Text(t("問題のあったファイル（{count} 件）", count=len(stage.problems)),
                    size=13, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_800)
        )
        items.append(_table(
            [(t("対象"), 240), (t("結果"), 80), (t("詳細"), 320)],
            [
                [
                    _mono(e.target or t("パッケージ全体")),
                    ft.Text(e.outcome, size=12, color=ft.Colors.ORANGE_800),
                    ft.Text(messages.event_detail(e.detail), size=11, color=_LABEL_COLOR),
                ]
                for e in stage.problems
            ],
        ))
    else:
        items.append(ft.Text(t("問題のあったファイルはありません。"), size=12,
                             color=ft.Colors.GREEN_700))
    return ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)


def _workflow_tab(report: package_report.PackageReport) -> ft.Control:
    """処理の記録を段階ごとの流れ図にする。

    表（処理の記録タブ）は 1 件ずつ並ぶので、原本が数百件あると
    「どの段階で何が起きたか」が掴めない。段階で束ねて、問題のある段階だけ
    色を変える。発端は iPRES 2026 の CloudViPER ワークショップでの
    キムさんの提案。
    """
    if not report.events:
        return _empty(
            t("処理の記録がありません"),
            t("SIP の段階では、処理の記録はまだありません。"
              "この SIP から AIP を作ると、行った処理がここに並びます。"),
        )

    stages = package_report.workflow(report)
    detail = ft.Container(expand=True, padding=ft.Padding.only(left=16, right=16))
    boxes: list[ft.Container] = []

    def select(index: int) -> None:
        for i, box in enumerate(boxes):
            box.border = ft.Border.all(
                2 if i == index else 1,
                ft.Colors.PRIMARY if i == index else ft.Colors.OUTLINE_VARIANT,
            )
        detail.content = _stage_detail(stages[index])

    def on_click(index: int):
        def handler(_e: ft.ControlEvent) -> None:
            select(index)
            flow.update()
            detail.update()
        return handler

    chain: list[ft.Control] = []
    for i, stage in enumerate(stages):
        icon, color = _stage_status(stage)
        if stage.problems:
            count = t("要確認 {count} 件", count=len(stage.problems))
        elif stage.recorded:
            count = t("{count} 件", count=stage.files)
        else:
            count = t("記録なし")
        box = ft.Container(
            ft.Column(
                [
                    ft.Icon(icon, size=20, color=color),
                    ft.Text(_stage_label(stage.event_type), size=12,
                            weight=ft.FontWeight.W_500,
                            text_align=ft.TextAlign.CENTER),
                    ft.Text(count, size=11, color=color),
                ],
                spacing=2,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            width=120,
            padding=ft.Padding.symmetric(horizontal=8, vertical=10),
            border_radius=8,
            ink=True,
            on_click=on_click(i),
        )
        boxes.append(box)
        if i:
            chain.append(ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=_LABEL_COLOR))
        chain.append(box)

    # 最初に目を向けるべき段階を開いておく。問題が無ければ先頭。
    first = next((i for i, s in enumerate(stages) if s.problems), 0)
    select(first)

    flow = ft.Row(chain, spacing=6, wrap=True, run_spacing=8,
                  vertical_alignment=ft.CrossAxisAlignment.CENTER)
    head = ft.Text(
        t("段階を押すと、使ったツール・件数・問題のあったファイルが出ます。"),
        size=12, color=_LABEL_COLOR,
    )
    return ft.Column(
        [
            ft.Container(ft.Column([head, flow], spacing=10),
                         padding=ft.Padding.only(left=16, right=16, top=12)),
            ft.Divider(height=16),
            detail,
        ],
        expand=True,
        spacing=0,
    )


def _files_tab(report: package_report.PackageReport) -> ft.Control:
    if not report.files:
        return _empty(t("ファイルの一覧を読めませんでした"), report.overview.note)

    def open_row(path: str):
        def handler(_e: ft.ControlEvent) -> None:
            target = report.root / path
            if not target.exists():
                target = report.root / "data" / path
            if target.exists():
                plat.reveal_in_file_manager(target)
        return handler

    rows = []
    for f in report.files:
        warning = ft.Text("", size=11)
        # 比べている相手は core が CSV に書いた値。ここを訳すと突合が外れる。
        if f.warning and f.warning not in ("", "-", raw("なし")):
            # siegfried の原文（PRONOM の番号が並ぶ英文）は読めないので言い換え、
            # 原文は指を載せたときに出す。
            warning = ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=14,
                            color=ft.Colors.ORANGE_700),
                    ft.Text(messages.format_warning(f.warning), size=11,
                            color=ft.Colors.ORANGE_800, tooltip=f.warning),
                ],
                spacing=4,
            )
        rows.append([
            ft.Text(messages.value(f.use), size=11, color=_LABEL_COLOR),
            _mono(f.path, 12),
            # METS には特定できなかった形式が "unknown" と書かれる（SIP の CSV では空欄）。
            # 同じ意味なので同じ言葉で出す。
            ft.Text(f.format_name if f.format_name not in ("", "unknown") else t("不明"),
                    size=12),
            _mono(f.puid),
            ft.Text(package_report.human_bytes(f.size), size=12),
            ft.Text(messages.value(f.virus) or "-", size=11, color=_LABEL_COLOR),
            warning,
            _mono((f.sha256[:12] + "…") if f.sha256 else ""),
            ft.IconButton(
                ft.Icons.FOLDER_OPEN, icon_size=16, tooltip=t("場所を開く"),
                on_click=open_row(f.path),
            ),
        ])

    head = ft.Container(
        ft.Text(
            t("{count} 件。中身を見るときは、右端のボタンでファイルの場所を開きます。",
              count=len(report.files)),
            size=12, color=_LABEL_COLOR,
        ),
        padding=ft.Padding.only(left=16, top=12, bottom=4),
    )
    return ft.Column(
        [head, _table(
            [(t("区分"), 70), (t("相対パス"), 260), (t("フォーマット"), 160), ("PRONOM", 90),
             (t("サイズ"), 80), (t("ウイルス検査"), 90), (t("警告"), 120),
             ("SHA-256", 120), ("", 40)],
            rows,
        )],
        expand=True,
        spacing=0,
    )


def _empty(title: str, note: str = "") -> ft.Control:
    return ft.Container(
        ft.Column(
            [
                ft.Icon(ft.Icons.INFO_OUTLINE, size=32, color=_LABEL_COLOR),
                ft.Text(title, size=13),
                ft.Text(note, size=11, color=_LABEL_COLOR),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )


def build(root: Path, *, on_close: Callable[[], None]) -> ft.Control:
    """ビューアを組み立てて返す。"""
    report = package_report.read(root)

    tabs = ft.Tabs(
        length=5,
        selected_index=0,
        expand=True,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(
                    tabs=[
                        ft.Tab(label=t("概要"), icon=ft.Icons.INVENTORY_2_OUTLINED),
                        ft.Tab(label=t("処理の記録"), icon=ft.Icons.HISTORY),
                        ft.Tab(label=t("ワークフロー"), icon=ft.Icons.ACCOUNT_TREE_OUTLINED),
                        ft.Tab(label=t("ファイル"), icon=ft.Icons.LIST_ALT_OUTLINED),
                        ft.Tab(label=t("生データ"), icon=ft.Icons.CODE),
                    ]
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        _overview_tab(report),
                        _events_tab(report),
                        _workflow_tab(report),
                        _files_tab(report),
                        _raw_browser(root),
                    ],
                ),
            ],
        ),
    )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, size=18),
                    ft.Text(t("生成結果: {name}", name=root.name),
                            weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.TextButton(t("閉じる"), icon=ft.Icons.CLOSE,
                                  on_click=lambda _e: on_close()),
                ],
                spacing=8,
            ),
            ft.Divider(height=1),
            tabs,
        ],
        expand=True,
        spacing=8,
    )
