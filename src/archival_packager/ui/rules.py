"""変換の規則の画面。いま効いている規則を見せ、規則のファイルを開き、読み直す。

## なぜ要るか

利用者の規則（rules.json。以前の rules.toml も読む）は、これまで画面から一切たどれなかった。置き場所は
隠しフォルダの中で、書き換えたらアプリを開き直すしかなく、書き間違いは AIP を
作ったあとの警告で初めて分かった。「書いたのに効かない」ときに、利用者には
確かめる手立てが無かった。

## 規則を画面で編集させない

入力欄で規則を組み立てる画面は作らない。規則が書けても、変換するコマンドは
利用者が別に入れる必要があり、難しいのはむしろそちらにある。入力欄を作るぶん
だけ書き間違いの入り口も増える。ここではファイルを開くところまでを受け持ち、
書いた結果（効いたか、なぜ効かないか）を見せることに力を割く。

## 読み直しは、処理の途中にはさせない

1 つの AIP の前半と後半で違う規則が効くと、説明の付かない差が生まれる
（core/conversion_registry.py の _table の説明）。処理中はボタンを押せなくする。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import flet as ft

from ..core import conversion_registry, fpr, normalizer, rule_table
from ..core.aip_models import Executor, NormalizationRule, RuleSource
from ..i18n import raw, t
from . import platform

_MONO = "Menlo, Consolas, monospace"

def _template() -> str:
    """規則のファイルが無いときに作るひな形（Archivematica の FPR の書式）。

    **例の規則はすべて "enabled": false にしておく。** 開いただけで規則が 1 つ効き
    始めると、利用者の意図しない変換が AIP に入る。JSON にはコメントが書けないので、
    読み方は command の description に書く。また、この形のまま Archivematica に
    `manage.py loaddata` で読ませられる（pk を書いていないので、2 回読ませると
    同じ内容が 2 つできる）。UUID は例の物。自分の規則を足すときは新しく作る。
    """
    tool = "6f1b1a1e-31c0-5e1a-9d2c-0a7d2b8c4e01"
    convert = "6f1b1a1e-31c0-5e1a-9d2c-0a7d2b8c4e02"
    verify = "6f1b1a1e-31c0-5e1a-9d2c-0a7d2b8c4e03"
    detail = "6f1b1a1e-31c0-5e1a-9d2c-0a7d2b8c4e04"
    script = "\n".join([
        "set -euo pipefail",
        'work=$(mktemp -d); trap \'rm -rf "$work"\' EXIT',
        "soffice --headless --norestore --nologo \"-env:UserInstallation=file://$work/profile\" "
        "--convert-to 'pdf:writer_pdf_Export:{\"SelectPdfVersion\":{\"type\":\"long\",\"value\":\"2\"}}' "
        '--outdir "$work/out" "%fileFullName%" >/dev/null 2>&1',
        'res=("$work"/out/*.pdf)',
        '[ -f "${res[0]}" ] || { echo "LibreOffice did not write a PDF" >&2; exit 1; }',
        'mv "${res[0]}" "%outputLocation%"',
    ])
    stamp = "2026-11-26T00:00:00Z"

    def command(uuid: str, description: str, text: str, usage: str, **more: object) -> dict:
        fields = {
            "uuid": uuid, "replaces": None, "enabled": False, "lastmodified": stamp,
            "tool": tool, "description": description, "command": text,
            "script_type": "bashScript", "output_location": None, "output_format": None,
            "command_usage": usage, "verification_command": None, "event_detail_command": None,
        }
        fields.update(more)
        return {"model": "fpr.fpcommand", "fields": fields}

    records = [
        {"model": "fpr.fptool", "fields": {
            "uuid": tool, "description": "LibreOffice", "version": "24.x", "enabled": False,
            "slug": "libreoffice-example"}},
        command(
            detail, "Get event detail text for LibreOffice",
            'echo program=\\"LibreOffice\\"\\; version=\\"`soffice --version | head -1`\\"',
            "event_detail"),
        command(
            verify, "Verify the PDF written by LibreOffice (file exists, PDF header)",
            'test -s "%outputLocation%" && head -c 5 "%outputLocation%" | grep -q "%PDF-"',
            "verification"),
        command(
            convert,
            "EXAMPLE (disabled): DOCX to PDF/A-2b with LibreOffice. On macOS, --headless may drop "
            "Japanese glyphs; test with your own documents before enabling.",
            script, "normalization",
            output_location="%outputDirectory%%prefix%%fileName%%postfix%.pdf",
            output_format="efcb5e46-b594-49ca-902e-408900abc23d",
            verification_command=verify, event_detail_command=detail),
        {"model": "fpr.fprule", "fields": {
            "uuid": "6f1b1a1e-31c0-5e1a-9d2c-0a7d2b8c4e05", "replaces": None, "enabled": False,
            "lastmodified": stamp, "purpose": "preservation",
            "format": "477c4b05-06eb-4ed5-913b-752e6d71845e", "command": convert,
            "count_attempts": 0, "count_okay": 0, "count_not_okay": 0}},
    ]
    return json.dumps(records, ensure_ascii=False, indent=2) + "\n"


#: ひな形の文面（モジュールの読み込みで 1 回作る。画面ごとに訳さない印の raw()）。
TEMPLATE = raw(_template())


def ensure_user_file() -> Path:
    """規則のファイル（rules.json）を返す。無ければひな形を書いてから返す。

    既にあるファイルには触れない（利用者の書いたものを上書きしない）。
    旧書式の rules.toml が既にあっても、新しく開くのは rules.json（TOML は読み続ける）。
    """
    path = rule_table.user_json_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    return path


def disabled_count(json_text: str) -> int:
    """rules.json の中で、無効（"enabled": false）にしてある規則（fpr.fprule）の数。"""
    if not json_text:
        return 0
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return 0  # 壊れている件は、読み込みの警告として別に出る
    if not isinstance(data, list):
        return 0
    return sum(
        1 for r in data
        if isinstance(r, dict) and r.get("model") == "fpr.fprule"
        and isinstance(r.get("fields"), dict) and r["fields"].get("enabled") is False
    )


def _grouped(table: rule_table.RuleTable) -> list[tuple[str, list[NormalizationRule]]]:
    """PUID ごとの規則を、表の 1 行（id）ごとにまとめる。並びは読み込んだ順。"""
    groups: dict[str, list[NormalizationRule]] = {}
    for rule in table.rules.values():
        groups.setdefault(rule.rule_id, []).append(rule)
    return list(groups.items())


def _tool_status(rule: NormalizationRule) -> tuple[bool, str]:
    """変換に使うコマンドが見つかるか。見つからない規則は、AIP では原本のまま残る。"""
    if rule.executor is Executor.BUILTIN:
        return True, t("アプリに内蔵の変換を使います")
    if rule.executor is Executor.FPR:
        # スクリプトの中で呼ぶコマンド（soffice など）までは、走らせる前には分からない。
        # 確かめられるのは、スクリプトを走らせる bash があるところまで。
        spec = rule.fpr
        if spec is not None and spec.command.script_type == "bashScript" and not fpr.bash_available():
            return False, t("bash が見つかりません")
        return True, t("rules.json のコマンド（Archivematica の FPR の書式）で変換します")
    found = normalizer.locate(rule.tool)
    if found is None:
        return False, t("{tool} が見つかりません", tool=rule.tool)
    return True, str(found)


def _rule_card(rule_id: str, rules: list[NormalizationRule]) -> ft.Control:
    first = rules[0]
    ok, where = _tool_status(first)
    source = t("自分で足した規則") if first.source is RuleSource.USER else t("最初から入っている規則")
    puids = ", ".join(r.puid_in for r in rules)
    target = first.format_name_out or first.puid_out or first.out_extension
    command = " ".join([first.tool, *first.args])
    fpr_description = first.fpr.description if first.fpr is not None else ""
    # FPR の規則の id は UUID なので、見出しには説明（無ければ UUID）を出す。
    # UUID は PREMIS の記録と突き合わせるために、道具の行に残す。
    title = (fpr_description or rule_id) if first.executor is Executor.FPR else rule_id
    return ft.Container(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CHECK_CIRCLE if ok else ft.Icons.ERROR_OUTLINE,
                                color=ft.Colors.GREEN if ok else ft.Colors.ERROR, size=18),
                        ft.Text(title, weight=ft.FontWeight.BOLD, size=13,
                                font_family=None if first.executor is Executor.FPR else _MONO),
                        ft.Text(source, size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    spacing=8,
                ),
                ft.Text(t("対象の形式: {puids}", puids=puids), size=12, selectable=True),
                ft.Text(t("変換後: {target}（.{ext}）", target=target, ext=first.out_extension),
                        size=12, selectable=True),
                # 内蔵の変換（Pillow）は外から呼ぶコマンドではないので、コマンドの行を出さない
                *([ft.Text(t("コマンド: {command}", command=command), size=12,
                           font_family=_MONO, selectable=True)]
                  if first.executor is Executor.COMMAND else []),
                # FPR の規則は、道具（fptool）の名前と、規則の UUID を出す
                *([ft.Text(t("道具: {tool} / 規則の UUID: {uuid}", tool=first.tool, uuid=rule_id),
                           size=12, selectable=True)]
                  if first.executor is Executor.FPR else []),
                ft.Text(where if ok else t("{where}。この規則の対象のファイルは、"
                                           "変換せず原本のまま保存されます。", where=where),
                        size=11, selectable=True,
                        color=ft.Colors.ON_SURFACE_VARIANT if ok else ft.Colors.ERROR),
            ],
            spacing=3,
        ),
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=6,
        padding=10,
    )


def build(*, on_close: Callable[[], None], is_busy: Callable[[], bool]) -> ft.Control:
    """画面を組み立てる。is_busy が真の間は、読み直しを押させない。"""
    path = rule_table.user_json_path()
    status = ft.Text("", size=12)
    body = ft.Column(spacing=8)

    def fill(table: rule_table.RuleTable) -> None:
        body.controls.clear()
        if table.warnings:
            body.controls.append(
                ft.Container(
                    ft.Column(
                        [ft.Text(t("読めなかった規則があります（その規則は使いません）"),
                                 weight=ft.FontWeight.BOLD, size=13, color=ft.Colors.ERROR),
                         *(ft.Text(w, size=12, selectable=True) for w in table.warnings)],
                        spacing=4,
                    ),
                    border=ft.Border.all(1, ft.Colors.ERROR),
                    border_radius=6,
                    padding=10,
                )
            )
        body.controls.append(ft.Text(t("いま効いている規則"), weight=ft.FontWeight.BOLD, size=14))
        body.controls.extend(_rule_card(rule_id, rules) for rule_id, rules in _grouped(table))
        disabled = disabled_count(table.json_document)
        if disabled:
            # ひな形の例は無効にしてある。読み直しても一覧が変わらないと、
            # 何も起きなかったように見えるので、無効の規則があることだけは知らせる。
            body.controls.append(ft.Text(
                t("rules.json に、無効にしてある規則が {n} 件あります（\"enabled\": false）。"
                  "使うには true にして、読み直してください。", n=disabled),
                size=12, color=ft.Colors.ON_SURFACE_VARIANT))

    def on_open(_e: ft.ControlEvent) -> None:
        try:
            platform.open_text(ensure_user_file())
        except OSError as exc:
            status.value = t("規則のファイルを作れませんでした: {error}", error=exc)
            status.color = ft.Colors.ERROR
            status.update()

    def on_reveal(_e: ft.ControlEvent) -> None:
        try:
            platform.reveal_in_file_manager(ensure_user_file())
        except OSError as exc:
            status.value = t("規則のファイルを作れませんでした: {error}", error=exc)
            status.color = ft.Colors.ERROR
            status.update()

    def on_reload(_e: ft.ControlEvent) -> None:
        if is_busy():
            status.value = t("処理の途中は読み直せません。終わってから押してください。")
            status.color = ft.Colors.ERROR
        else:
            table = conversion_registry.reload()
            fill(table)
            status.value = (
                t("読み直しました。読めなかった規則が {count} 件あります。", count=len(table.warnings))
                if table.warnings else t("読み直しました。次に作る AIP から、この規則が使われます。"))
            status.color = ft.Colors.ERROR if table.warnings else ft.Colors.PRIMARY
        status.update()
        body.update()

    fill(conversion_registry.table())

    return ft.Container(
        ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(t("変換の規則"), weight=ft.FontWeight.BOLD, size=18),
                        ft.Container(expand=True),
                        ft.TextButton(t("閉じる"), icon=ft.Icons.CLOSE, on_click=lambda _e: on_close()),
                    ]
                ),
                ft.Text(t("AIP を作るとき、どの形式をどう変換するかの規則です。"
                          "自分で規則を足すには、規則のファイルに書いて保存し、"
                          "「読み直す」を押します。"), size=12),
                ft.Text(t("規則のファイル: {path}", path=path), size=12,
                        font_family=_MONO, selectable=True),
                ft.Row(
                    [
                        ft.FilledButton(t("規則のファイルを開く"), icon=ft.Icons.EDIT_NOTE,
                                        on_click=on_open),
                        ft.OutlinedButton(t("場所を開く"), icon=ft.Icons.FOLDER_OPEN,
                                          on_click=on_reveal),
                        ft.OutlinedButton(t("読み直す"), icon=ft.Icons.REFRESH,
                                          on_click=on_reload),
                    ],
                    spacing=8,
                    wrap=True,
                ),
                status,
                ft.Divider(),
                body,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=20,
        expand=True,
    )
