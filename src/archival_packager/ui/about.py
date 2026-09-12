"""情報画面。使い方・ライセンス・連絡先。

**ライセンスの表示は義務である。** 同梱している ClamAV は GPL-2.0 で、
表示とソース入手手段の提示が求められる。NOTICE を実行ファイルの隣に置くだけでは、
Microsoft ストアから入れた利用者は辿り着けない（インストール先はシステムの奥で、
普通は開かない）。画面から読めるようにする。

起動時に一度だけ見せる形（ランディングページ）にはしない。一度通り過ぎると
戻れず、ライセンス表示の目的に合わないため。いつでも開ける場所に置く。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ..core import applog

_MONO = "Menlo, Consolas, monospace"

PRIVACY_URL = "https://nakamura196.github.io/archival-packager/privacy-policy.html"
STORE_URL = "https://apps.microsoft.com/detail/9N6XJD7THHPZ"
CONTACT = "nakamura@hi.u-tokyo.ac.jp"

_USAGE = [
    ("1. 何を作るかを選ぶ",
     "受入パッケージ（SIP）だけを作るか、長期保存パッケージ（AIP）まで作るかを選びます。"),
    ("2. 入力と出力先を選ぶ",
     "素材のフォルダ（または ZIP）と、成果物を置くフォルダを指定します。"
     "原本は読み取るだけで、変更しません。"),
    ("3. 記述メタデータを入れる",
     "タイトルなどを入力します。ここで入れた内容が、受入記録として"
     "パッケージに残ります。"),
    ("4. 実行する",
     "フォーマットの識別、チェックサムの算出、必要なら検査を行い、"
     "情報パッケージを作ります。"),
    ("5. 中身を確かめる",
     "できあがったら「中身を見る」で、生成された構造とファイルの内容を"
     "そのまま読めます。"),
]


def _doc(name: str) -> str:
    path = applog.document_path(name)
    if path is None:
        return f"（{name} が見つかりませんでした）"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"（{name} を読めませんでした: {exc}）"


def _link(label: str, url: str) -> ft.Control:
    return ft.TextButton(
        label,
        icon=ft.Icons.OPEN_IN_NEW,
        url=url,
        style=ft.ButtonStyle(padding=ft.Padding.all(4)),
    )


def _panel(*controls: ft.Control) -> ft.Control:
    return ft.Container(
        ft.Column(list(controls), spacing=8),
        padding=16,
    )


def build(*, on_close: Callable[[], None]) -> ft.Control:
    usage = ft.Column(
        [
            ft.Column(
                [
                    ft.Text(heading, weight=ft.FontWeight.BOLD, size=13),
                    ft.Text(body, size=12),
                ],
                spacing=2,
            )
            for heading, body in _USAGE
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )

    def _license_view(name: str) -> ft.Control:
        return ft.Column(
            [ft.Text(_doc(name), size=11, font_family=_MONO, selectable=True)],
            scroll=ft.ScrollMode.AUTO,
        )

    # Flet 0.86 の Tabs は、見出し（TabBar）と中身（TabBarView）を
    # content の中に自分で並べる形。Tab は見出しだけで中身を持たない。
    tabs = ft.Tabs(
        length=3,
        selected_index=0,
        expand=True,
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="使い方", icon=ft.Icons.HELP_OUTLINE),
                        ft.Tab(label="このアプリについて", icon=ft.Icons.INFO_OUTLINE),
                        ft.Tab(label="ライセンス", icon=ft.Icons.GAVEL),
                    ]
                ),
                ft.TabBarView(
                    expand=True,
                    controls=[
                        _panel(usage),
                        _panel(
                            ft.Text(applog.environment(), size=12, selectable=True),
                            ft.Text(
                                "デジタル資料から、国際標準 OAIS の情報パッケージを"
                                "作成します。",
                                size=12,
                            ),
                            ft.Text(
                                "開発: 中村 覚（東京大学）・金 甫榮（人間文化研究機構）",
                                size=12,
                            ),
                            ft.Divider(height=1),
                            ft.Text("連絡先", weight=ft.FontWeight.BOLD, size=13),
                            ft.Text(CONTACT, size=12, selectable=True),
                            ft.Text(
                                "不具合に出会われたら、エラー画面の「内容をコピー」から"
                                "貼り付けてお送りください。",
                                size=11,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            ft.Row(
                                [
                                    _link("プライバシーポリシー", PRIVACY_URL),
                                    _link("Microsoft ストア", STORE_URL),
                                ],
                                wrap=True,
                            ),
                            ft.Text(
                                f"記録の保存先: {applog.log_path()}",
                                size=11, selectable=True,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ),
                        _panel(
                            ft.Text(
                                "本アプリは MIT ライセンスです。同梱している第三者の"
                                "コンポーネントには、それぞれ元のライセンスが適用されます。"
                                "とくに ClamAV は GPL-2.0 であり、ソースコードの入手方法を"
                                "下記に示しています。",
                                size=12,
                            ),
                            ft.Divider(height=1),
                            ft.Container(_license_view("LICENSE"), height=140),
                            ft.Divider(height=1),
                            ft.Container(_license_view("NOTICE"), expand=True),
                        ),
                    ],
                ),
            ],
        ),
    )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=18),
                    ft.Text("情報", weight=ft.FontWeight.BOLD),
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
