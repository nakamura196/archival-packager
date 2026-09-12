"""生成結果のビューア。

**ファイルを開くのは Finder / エクスプローラーに任せる。**
ここが見せるのは、それらでは見えないもの＝ METS と PREMIS の中身。
原本は PDF や Word や画像なので、どのみちアプリの中では開けない。
ファイルを並べるだけなら OS の方が便利で、それと張り合っても意味がない。

  概要     何がいくつ入っているか
  処理の記録  いつ・何を・どのツールで行い、結果はどうだったか（PREMIS）
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
    for unit in ("バイト", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "バイト" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return f"{n} バイト"


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
            body.controls.append(ft.Text(f"読み取れませんでした: {exc}", size=12))
            body.update()
            return

        if isinstance(content, preview.BinaryPreview):
            body.controls.append(
                ft.Column(
                    [
                        ft.Text(path.name, weight=ft.FontWeight.BOLD),
                        ft.Text("テキストとして表示できない形式です", size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(f"サイズ: {_human_size(content.size_bytes)}", size=12),
                        ft.Text(f"先頭バイト: {content.head_hex}", size=11,
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
                        f"※ 先頭 {_human_size(preview.MAX_BYTES)} のみ表示しています。"
                        "全体はファイルを直接お開きください。",
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
                ft.Text("左のファイルを選ぶと内容を表示します", size=12,
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
            ft.Text(value or "（未記入）", size=12, selectable=True),
        ],
        spacing=8,
    )


def _table(columns: list[tuple[str, int]], rows: list[list[ft.Control]]) -> ft.Control:
    """横に長くなるので、表そのものを横スクロールさせる。"""
    table = ft.DataTable(
        columns=[ft.DataColumn(ft.Text(name, size=12, weight=ft.FontWeight.BOLD))
                 for name, _w in columns],
        rows=[ft.DataRow(cells=[ft.DataCell(c) for c in cells]) for cells in rows],
        heading_row_height=36,
        data_row_min_height=32,
        data_row_max_height=48,
        column_spacing=18,
    )
    return ft.Row([table], scroll=ft.ScrollMode.AUTO, expand=True)


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
        out.append((f"その他 {len(rest)} 種", sum(n for _name, n in rest), _OTHER_COLOR))
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
                        ft.Text("原本", size=11, color=_LABEL_COLOR),
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
                    ft.Text(f"{count} 件", size=12, color=_LABEL_COLOR),
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
        _stat("保存用に変換", f"{summary.normalized} 件"),
        _stat("ウイルス検査済", f"{summary.virus_scanned} 件"),
    ]
    if summary.unidentified:
        stats.append(_stat("未識別", f"{summary.unidentified} 件", warn=True))
    if summary.extension_warnings:
        stats.append(_stat("拡張子が不一致", f"{summary.extension_warnings} 件", warn=True))

    return ft.Column(
        [
            ft.Text("内訳", size=13, weight=ft.FontWeight.BOLD),
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
            {"AIP": "保存用情報パッケージ（AIP）",
             "SIP": "提出用情報パッケージ（SIP）"}.get(o.kind, o.kind),
            size=12, color=_LABEL_COLOR,
        ),
        ft.Divider(height=16),
        _pair("識別子", o.identifier),
        _pair("作成日時", o.created),
        _pair(
            "ファイル数",
            f"原本 {o.original_count} 件"
            + (f"（ほかに {o.file_count - o.original_count} 件）"
               if o.file_count > o.original_count else ""),
        ),
        _pair("合計サイズ", package_report.human_bytes(o.total_bytes)),
        _pair("置き場所", str(report.root)),
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
                        "フォルダを開く",
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
            "処理の記録がありません",
            "AIP には PREMIS の記録が入ります。SIP の段階では作られません。",
        )

    rows = [
        [
            _mono(e.date_time),
            ft.Text(e.type_label, size=12, weight=ft.FontWeight.W_500),
            ft.Text(e.outcome, size=12),
            ft.Text(e.agent, size=12, color=_LABEL_COLOR),
            _mono(e.target or "パッケージ全体"),
            ft.Text(e.detail, size=11, color=_LABEL_COLOR),
        ]
        for e in report.events
    ]
    kinds = "、".join(f"{name} {n}" for name, n in report.summary.events)
    head = ft.Container(
        ft.Column(
            [
                ft.Text(
                    f"{len(report.events)} 件の記録。"
                    "担当者が別に作業記録を書く必要はありません。",
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
            [("日時", 160), ("処理", 120), ("結果", 80), ("実行したもの", 140),
             ("対象", 200), ("詳細", 240)],
            rows,
        )],
        expand=True,
        spacing=0,
    )


def _files_tab(report: package_report.PackageReport) -> ft.Control:
    if not report.files:
        return _empty("ファイルの一覧を読めませんでした", report.overview.note)

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
        if f.warning and f.warning not in ("", "-", "なし"):
            warning = ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=14,
                            color=ft.Colors.ORANGE_700),
                    ft.Text(f.warning, size=11, color=ft.Colors.ORANGE_800),
                ],
                spacing=4,
            )
        rows.append([
            ft.Text(f.use, size=11, color=_LABEL_COLOR),
            _mono(f.path, 12),
            ft.Text(f.format_name or "不明", size=12),
            _mono(f.puid),
            ft.Text(package_report.human_bytes(f.size), size=12),
            ft.Text(f.virus or "-", size=11, color=_LABEL_COLOR),
            warning,
            _mono((f.sha256[:12] + "…") if f.sha256 else ""),
            ft.IconButton(
                ft.Icons.FOLDER_OPEN, icon_size=16, tooltip="場所を開く",
                on_click=open_row(f.path),
            ),
        ])

    head = ft.Container(
        ft.Text(
            f"{len(report.files)} 件。中身を見るときは、右端のボタンで"
            "ファイルの場所を開きます。",
            size=12, color=_LABEL_COLOR,
        ),
        padding=ft.Padding.only(left=16, top=12, bottom=4),
    )
    return ft.Column(
        [head, _table(
            [("区分", 70), ("相対パス", 260), ("フォーマット", 160), ("PRONOM", 90),
             ("サイズ", 80), ("ウイルス検査", 90), ("警告", 120),
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
        length=4,
        selected_index=0,
        expand=True,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="概要", icon=ft.Icons.INVENTORY_2_OUTLINED),
                        ft.Tab(label="処理の記録", icon=ft.Icons.HISTORY),
                        ft.Tab(label="ファイル", icon=ft.Icons.LIST_ALT_OUTLINED),
                        ft.Tab(label="生データ", icon=ft.Icons.CODE),
                    ]
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        _overview_tab(report),
                        _events_tab(report),
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
                    ft.Text(f"生成結果: {root.name}", weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    ft.TextButton("閉じる", icon=ft.Icons.CLOSE,
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
