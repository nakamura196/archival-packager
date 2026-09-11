"""生成結果のビューア。左にツリー、右に中身。

Swift 版（nakamura196/archival-packager）の ResultViewer.swift に対応する。
**読み取りと色分けは core/preview.py にある。** ここは並べ方だけを持つ。

大仙市アーカイブズでの聞き取りでは、生成された情報パッケージの中身を
画面上で見せたことが理解を助けた。パスを出すだけでは、何ができたのかが
伝わらない。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import flet as ft

from ..core import preview

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


def build(root: Path, *, on_close: Callable[[], None]) -> ft.Control:
    """ビューアを組み立てて返す。"""
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
            ft.Row(
                [
                    ft.Container(tree, width=340),
                    ft.VerticalDivider(width=1),
                    ft.Container(body, expand=True, padding=10),
                ],
                expand=True,
                spacing=0,
            ),
        ],
        expand=True,
        spacing=8,
    )
