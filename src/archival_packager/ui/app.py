"""Archival Packager の画面。

退役した Swift 実装の SwiftUI 画面（ArchivalPackagerApp / FullRootView / ContentView /
AIPContentView / ResultViewer）に由来する。

3 つのモードを 1 つのアプリに収める:

    1. SIP 作成          素材フォルダ（または ZIP）から受入パッケージを作る
    2. AIP 作成          SIP から長期保存パッケージを作る
    3. 素材から AIP まで  上記を続けて実行する

## 設計の要点

パイプラインは FS 走査とハッシュ計算を含むブロッキング処理なので、必ず別スレッドで
走らせる。UI スレッドで実行すると、大きな移管では画面が固まって「壊れた」と判断される。
進捗は progress コールバックで受け取り、UI スレッドへ反映する。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from .. import __version__, i18n
from ..core import aip_pipeline, applog, clamav, sip_pipeline, sip_reader
from ..core.aip_models import AIPOptions, AIPPipelineError, AIPResult, DescriptiveMetadata
from ..core.models import (
    SIPMetadata,
    SIPOptions,
    SIPPipelineError,
    SIPResult,
    is_same_or_inside,
)
from ..i18n import t
from . import about, messages, viewer
from . import platform as plat

MODE_SIP = "sip"
MODE_AIP = "aip"
MODE_FULL = "full"


@dataclass
class Selection:
    """画面で選ばれた入力・出力。"""

    input_path: Path | None = None
    output_parent: Path | None = None
    prior_accession: Path | None = None
    #: ビューアを開く手続き。組み立ての順番の都合で、レイアウトを作ったあとに入れる。
    #: show_result はそれより前に定義されるため、ここを経由して呼ぶ。
    open_viewer: Callable[[Path], None] = lambda _p: None
    #: 作った SIP を入力にして、AIP 作成へ移る手続き。open_viewer と同じ理由でここを経由する。
    continue_to_aip: Callable[[Path], None] = lambda _p: None

    #: 入力に選んだものの種類（"material" か "sip"）。モードを切り替えて
    #: 種類が変わったら、選んだ入力を外す（apply_mode を参照）。
    input_kind: str = ""

    #: page.add を済ませたか。Flet 0.86 の Control.page は、画面に載る前に
    #: 読むと None ではなく RuntimeError を投げる。判定に使えないので自分で持つ。
    on_page: bool = False


def main(page: ft.Page) -> None:
    page.title = "Archival Packager"
    # 既定のままだと Flet の素の見た目になる。落ち着いた青緑を基調にし、
    # OS のダークモードに追随させる（アーカイブズの現場は明るい部屋とは限らない）。
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.TEAL)
    page.dark_theme = ft.Theme(color_scheme_seed=ft.Colors.TEAL)
    page.theme_mode = ft.ThemeMode.SYSTEM
    page.padding = 0
    page.window.width = 1000
    # 820 だと「オプション」欄が画面の下で切れ、スクロールしないと見えなかった。
    # 13 インチのノート（1440x900 や 1280x800）でも収まる範囲で高くする。
    page.window.height = 880
    page.window.min_width = 760
    page.padding = 0

    state = Selection()

    # ------------------------------------------------------------------
    # 共通部品
    # ------------------------------------------------------------------

    progress_log = ft.ListView(expand=True, spacing=2, auto_scroll=True, padding=10)
    progress_bar = ft.ProgressBar(visible=False)
    run_button = ft.FilledButton(t("実行"), icon=ft.Icons.PLAY_ARROW, disabled=True)
    #: なぜ押せないのかを書く。灰色のボタンだけを見せられても、何が足りないのか
    #: 画面から分からない（「押せない」という報告を受けた）。
    run_hint = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
    result_panel = ft.Column(spacing=8)

    #: 進捗ログに残す行数の上限。処理の記録は report.txt に残るので、
    #: 画面は「いま何をしているか」が見えれば足りる。
    #: 上限を設けないと、数千ファイルの移管で行が数千に膨らみ、
    #: 1 回の更新にかかる時間が行数に比例して伸びる（実際に重くなった）。
    LOG_MAX_LINES = 300

    def ui(mutate, target=None) -> None:
        """画面の変更は必ずイベントループ側で行う。

        ワーカースレッドから直接 controls をいじると、UI 側が木構造を比較している
        最中にリストが伸び、Flet の差分計算が範囲外を見て落ちる
        （object_patch._compare_lists で IndexError）。**配布版で実際に起きた。**

        page.run_task は内部で asyncio.run_coroutine_threadsafe を使うので、
        どのスレッドから呼んでも安全にイベントループへ渡る。UI スレッドから
        呼んでも単に予約されるだけなので、呼び分けは不要。

        target を渡すと、そのコントロールだけを更新する。**page.update() は
        画面全体を比較するので、ログを 1 行足すたびに呼ぶと行数に比例して
        遅くなる**（1000 行入ったところで目に見えて重くなった）。
        更新する範囲は、変えた場所に絞る。
        """

        async def _run() -> None:
            mutate()
            (target or page).update()

        page.run_task(_run)

    def log(message: str) -> None:
        # core の進捗は日本語で来る。画面に出すときだけ今の言語にする。
        message = messages.progress_line(message)

        def _append() -> None:
            items = progress_log.controls
            items.append(ft.Text(message, size=12, selectable=True))
            if len(items) > LOG_MAX_LINES:
                del items[: len(items) - LOG_MAX_LINES]

        ui(_append, progress_log)

    def log_status(message: str) -> None:
        """途中経過を 1 行で書き換える。

        ウイルス定義の取得は数十 MB あり、freshclam は進捗を何度も出し直す。
        それを行として積むと数百行になり、「更新できたか」が埋もれる。
        直前も途中経過だったなら、その行を差し替える。
        """

        def _set() -> None:
            text = ft.Text(message, size=12, selectable=True,
                           color=ft.Colors.ON_SURFACE_VARIANT)
            text.data = "status"
            items = progress_log.controls
            if items and getattr(items[-1], "data", None) == "status":
                items[-1] = text
            else:
                items.append(text)

        ui(_set, progress_log)

    def clear_log() -> None:
        def _clear() -> None:
            progress_log.controls.clear()
            result_panel.controls.clear()

        ui(_clear)

    # ------------------------------------------------------------------
    # モード
    # ------------------------------------------------------------------

    mode = ft.RadioGroup(
        value=MODE_SIP,
        content=ft.Column(
            [
                ft.Radio(value=MODE_SIP,
                         label=t("SIP 作成（素材フォルダ／ZIP から受入パッケージ）")),
                ft.Radio(value=MODE_AIP, label=t("AIP 作成（SIP から長期保存パッケージ）")),
                ft.Radio(value=MODE_FULL, label=t("素材から AIP まで一気通貫")),
            ],
            spacing=2,
        ),
    )

    # ------------------------------------------------------------------
    # 記述メタデータ
    # ------------------------------------------------------------------

    identifier = ft.TextField(label=t("識別子"), hint_text=t("例: 2026-移管-総務課"), dense=True)
    title = ft.TextField(
        label=t("タイトル（必須）"), hint_text=t("例: 総務課 一般文書"), dense=True
    )
    scope_note = ft.TextField(
        label=t("内容・範囲"), multiline=True, min_lines=2, max_lines=4, dense=True
    )
    date_note = ft.TextField(label=t("年代"), hint_text=t("例: 2024–2025"), dense=True)
    archivist = ft.TextField(
        label=t("担当者名"),
        hint_text=t("PREMIS に保存処理の実施者として記録されます"),
        dense=True,
    )

    # ------------------------------------------------------------------
    # オプション
    # ------------------------------------------------------------------

    def option_box(label: str, value: bool) -> ft.Checkbox:
        """オプションのチェックボックス。

        **ラベルは Text で渡す。** 文字列のままだと 1 行に固定され、左の列
        （幅 420）に収まらない分が切れていた（「元名は accession.csv に残」で
        途切れ、英語ではさらに短い所で切れる）。Text なら折り返す。
        見出しの要約に使うため、文字列は data に持たせる。
        """
        # 幅は左の列（420）から余白とチェックの枠を引いたもの。幅を決めないと折り返さない。
        box = ft.Checkbox(label=ft.Text(label, width=300), value=value)
        box.data = label
        return box

    make_bag = option_box(t("BagIt bag として梱包する"), False)
    scan_pii = option_box(t("個人情報(PII)を走査する"), False)
    scan_virus = option_box(t("ウイルス検査を行う（定義 DB が必要）"), False)
    sanitize = option_box(t("ファイル名を安全化する（元名は accession.csv に残ります）"), False)
    serialize_zip = option_box(t("成果物を ZIP（無圧縮）に固める"), False)
    normalize = option_box(t("保存用フォーマットへ変換する（AIP）"), True)

    # ------------------------------------------------------------------
    # ウイルス定義データベース
    # ------------------------------------------------------------------
    #
    # 定義 DB は数百 MB あり、しかも日々更新される。アプリに同梱すると
    # 配布物が肥大化した上に、配った瞬間から古くなる。ここから取得する。
    #
    # 状態を常に見せるのは、「検査できなかった」を「ウイルスが無かった」と
    # 読み違えさせないため。チェックを入れていても DB が無ければ検査は走らない。

    def virus_db_message() -> str:
        if clamav.find_tool() is None:
            return t("ウイルス定義: ClamAV が同梱されていないため検査できません")
        return messages.virus_db_status(clamav.database_status())

    virus_db_status = ft.Text(
        virus_db_message(), size=12, color=ft.Colors.ON_SURFACE_VARIANT
    )
    virus_db_button = ft.OutlinedButton(
        t("定義を取得 / 更新"),
        icon=ft.Icons.CLOUD_DOWNLOAD,
        disabled=clamav.find_updater() is None,
    )

    # ------------------------------------------------------------------
    # ファイル選択
    # ------------------------------------------------------------------

    input_label = ft.Text(t("未選択"), size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    output_label = ft.Text(t("未選択"), size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    prior_label = ft.Text(t("未選択（任意）"), size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    #: 選んだものが使えないときの理由。欄のすぐ下に赤で出す。
    input_note = ft.Text("", size=12, color=ft.Colors.ERROR, visible=False)
    output_note = ft.Text("", size=12, color=ft.Colors.ERROR, visible=False)

    # FilePicker は「サービス」として page.services に登録し、選択結果は
    # コールバックではなく await の戻り値で受け取る（Flet 0.86 の API）。
    # 0.28 系の on_result= / FilePickerResultEvent は存在しない。
    picker = ft.FilePicker()
    page.services.append(picker)

    clipboard = ft.Clipboard()
    page.services.append(clipboard)

    def refresh_run_enabled() -> None:
        # **タイトルは必須。** 空のまま出力できると、記述の無い情報パッケージが
        # できてしまう。あとから資料を探す手がかりが無くなる。
        # AIP 作成では SIP の記述を引き継ぐので、ここでは求めない。
        needs_title = mode.value in (MODE_SIP, MODE_FULL)
        missing = []
        if state.input_path is None:
            missing.append(t("素材フォルダ") if mode.value != MODE_AIP else t("SIP フォルダ"))
        if state.output_parent is None:
            missing.append(t("出力先"))
        if needs_title and not (title.value or "").strip():
            missing.append(t("タイトル"))

        input_problem, output_problem = selection_problems()
        input_note.value = input_problem
        input_note.visible = bool(input_problem)
        output_note.value = output_problem
        output_note.visible = bool(output_problem)

        run_button.disabled = bool(missing) or bool(input_problem or output_problem)
        # 区切りも訳の対象。日本語の中黒をそのまま英語に出すと読めない。
        if missing:
            run_hint.value = t("あと {items} を指定すると押せます", items=t("・").join(missing))
        elif input_problem or output_problem:
            run_hint.value = t("入力と出力先の赤字の説明を確かめてください")
        else:
            run_hint.value = ""
        run_hint.visible = bool(run_hint.value)
        # 組み立ての途中（page.add より前）にも呼ばれる。まだ画面が無いうちは
        # 送らない。
        if state.on_page:
            page.update()

    def selection_problems() -> tuple[str, str]:
        """選んだ入力・出力先のままでは、実行しても失敗する（または原本を汚す）もの。

        押してから落ちるのではなく、選んだ時点で、その欄の下に理由を出す。
        返すのは (入力の欄に出す文, 出力先の欄に出す文)。
        """
        src, dest = state.input_path, state.output_parent
        output_problem = ""
        if src is not None and dest is not None and is_same_or_inside(dest, src):
            # **原本のフォルダに書き込ませない。** core でも止めるが、押す前に見せる。
            output_problem = (
                t("出力先が SIP のフォルダの中にあります。別の場所を選んでください。")
                if mode.value == MODE_AIP else
                t("出力先が資料のフォルダの中にあります。原本のフォルダに書き込まないよう、"
                  "別の場所を選んでください。")
            )
        input_problem = ""
        if src is not None and mode.value == MODE_AIP and not sip_reader.looks_like_sip(src):
            # よくある取り違えは 2 つ。SIP を入れた「出力先」のフォルダを選んだ場合と、
            # 素材のフォルダを選んだ場合。前者なら、中の SIP を名指しする。
            try:
                inside = [p for p in sorted(src.iterdir()) if sip_reader.looks_like_sip(p)]
            except OSError:
                inside = []
            if len(inside) == 1:
                input_problem = t(
                    "選んだフォルダは SIP ではありません。この中の「{name}」が SIP です。"
                    "そちらを選んでください。", name=inside[0].name)
            else:
                input_problem = t(
                    "選んだフォルダは SIP ではありません。素材のフォルダから作るときは、"
                    "「何を作るか」で「SIP 作成」か「素材から AIP まで一気通貫」を選んでください。")
            # 入力が SIP でないうちは、出力先について「SIP の中」と言うのは誤り。
            # 先に入力を直してもらう。
            output_problem = ""
        return input_problem, output_problem

    async def choose_input_dir(_e: ft.ControlEvent) -> None:
        chosen = await picker.get_directory_path(dialog_title=t("素材フォルダ / SIP を選ぶ"))
        if chosen:
            state.input_path = Path(chosen)
            input_label.value = chosen
        refresh_run_enabled()

    async def choose_input_zip(_e: ft.ControlEvent) -> None:
        files = await picker.pick_files(
            dialog_title=t("受入 ZIP を選ぶ"), allowed_extensions=["zip"], allow_multiple=False
        )
        if files:
            state.input_path = Path(files[0].path)
            input_label.value = files[0].path
        refresh_run_enabled()

    async def choose_output(_e: ft.ControlEvent) -> None:
        chosen = await picker.get_directory_path(dialog_title=t("出力先フォルダを選ぶ"))
        if chosen:
            state.output_parent = Path(chosen)
            output_label.value = chosen
        refresh_run_enabled()

    async def choose_prior(_e: ft.ControlEvent) -> None:
        files = await picker.pick_files(
            dialog_title=t("前回の accession.csv を選ぶ"),
            allowed_extensions=["csv"], allow_multiple=False,
        )
        if files:
            state.prior_accession = Path(files[0].path)
            prior_label.value = files[0].path
        page.update()

    # ------------------------------------------------------------------
    # 実行
    # ------------------------------------------------------------------

    def show_result(result: SIPResult | AIPResult) -> None:
        """成果物と、目視確認が必要な点を出す。

        ワーカースレッドから呼ばれる。いったん手元のリストに積み、
        最後にまとめてイベントループ側へ渡す（ui の説明を参照）。
        """
        _items: list[ft.Control] = []
        if isinstance(result, SIPResult):
            path = result.sip_path
            headline = t(
                "SIP を作成しました（{count} 件 / {size} バイト）",
                count=result.file_count, size=f"{result.total_bytes:,}",
            )
            extras = [
                (t("記述スプレッドシート"), result.spreadsheet_path),
                (t("レポート"), result.report_path),
                (t("PII レポート"), result.pii_report_path),
                (t("配列前後の対応表"), result.arrangement_map_path),
                ("ZIP", result.zip_path),
            ]
        else:
            path = result.aip_path
            headline = t(
                "AIP を作成しました（原本 {originals} 件 / 派生物 {derivatives} 件）",
                originals=result.original_count, derivatives=result.derivative_count,
            )
            extras = [("METS", result.mets_path), ("ZIP", result.zip_path)]

        _items.append(
            ft.Text(headline, weight=ft.FontWeight.BOLD, size=15)
        )
        _items.append(
            ft.Row(
                [
                    ft.Text(str(path), size=12, selectable=True, expand=True),
                    # パスを出すだけでは「何ができたか」が伝わらない。
                    # 中身を見せることが理解を助ける（大仙市での聞き取り）。
                    ft.FilledTonalButton(
                        t("中身を見る"),
                        icon=ft.Icons.FIND_IN_PAGE_OUTLINED,
                        on_click=lambda _e, p=path: state.open_viewer(p),
                    ),
                    ft.OutlinedButton(
                        t("場所を開く"),
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=lambda _e, p=path: plat.reveal_in_file_manager(p),
                    ),
                ]
            )
        )

        for label, candidate in extras:
            if candidate is None:
                continue
            _items.append(
                ft.Row(
                    [
                        ft.Text(f"{label}: {candidate.name}", size=12, expand=True),
                        ft.TextButton(
                            t("開く"), on_click=lambda _e, p=candidate: plat.open_path(p)
                        ),
                    ]
                )
            )

        if result.warnings:
            _items.append(
                ft.Container(
                    ft.Column(
                        [
                            ft.Text(
                                t("目視確認が必要な点: {count} 件", count=len(result.warnings)),
                                weight=ft.FontWeight.BOLD,
                            ),
                            *[ft.Text(t("・{warning}", warning=messages.warning_line(w)),
                                      size=12)
                              for w in result.warnings[:50]],
                            *(
                                [ft.Text(t("（他 {count} 件）",
                                           count=len(result.warnings) - 50), size=12)]
                                if len(result.warnings) > 50
                                else []
                            ),
                            # 種類の名前だけでは、何をすればよいかが分からない。
                            *(
                                [ft.Divider(height=8, color=ft.Colors.AMBER_200)]
                                + [ft.Text(a, size=11, color=ft.Colors.ON_SURFACE_VARIANT)
                                   for a in advice]
                                if (advice := messages.warning_advice(result.warnings))
                                else []
                            ),
                        ],
                        spacing=2,
                    ),
                    bgcolor=ft.Colors.AMBER_50,
                    border=ft.Border.all(1, ft.Colors.AMBER_400),
                    border_radius=6,
                    padding=10,
                )
            )
        else:
            _items.append(
                ft.Text(t("目視確認が必要な点はありません。"), size=12,
                        color=ft.Colors.GREEN_700)
            )

        # **次にすることを置く。** SIP を作ったあと、AIP 作成へ移るには、
        # モードを切り替え、いま作った SIP のフォルダを探して選び直す必要があった。
        # 出力先のフォルダの中の、どれが SIP なのかで迷う（素材のフォルダを
        # 選んだまま実行しかけた）。作った SIP をそのまま渡す。
        if isinstance(result, SIPResult) and mode.value == MODE_SIP:
            _items.append(
                ft.Container(
                    ft.Row(
                        [
                            ft.FilledButton(
                                t("この SIP から AIP を作る"),
                                icon=ft.Icons.ARROW_FORWARD,
                                on_click=lambda _e, p=path: state.continue_to_aip(p),
                            ),
                            ft.Text(
                                t("中身を確かめてから進んでください。"
                                  "日を改めるときは「AIP 作成」でこのフォルダを選びます。"),
                                size=11, color=ft.Colors.ON_SURFACE_VARIANT, expand=True,
                            ),
                        ],
                        spacing=10,
                    ),
                    padding=ft.Padding.only(top=6),
                )
            )
        ui(lambda: result_panel.controls.extend(_items))

    def worker() -> None:
        """別スレッドで走る本処理。UI を固めないため。"""
        try:
            assert state.input_path and state.output_parent
            selected = mode.value

            sip_result: SIPResult | None = None
            if selected in (MODE_SIP, MODE_FULL):
                sip_result = sip_pipeline.run(
                    input_path=state.input_path,
                    output_parent=state.output_parent,
                    metadata=SIPMetadata(
                        identifier=identifier.value or "",
                        title=title.value or "",
                        scope_note=scope_note.value or "",
                        date_note=date_note.value or "",
                    ),
                    options=SIPOptions(
                        make_bag=make_bag.value,
                        scan_pii=scan_pii.value,
                        scan_virus=scan_virus.value,
                        sanitize_filenames=sanitize.value,
                        serialize_zip=serialize_zip.value and selected == MODE_SIP,
                        prior_accession_path=state.prior_accession,
                    ),
                    progress=log,
                )
                if selected == MODE_SIP:
                    show_result(sip_result)

            if selected in (MODE_AIP, MODE_FULL):
                # 一気通貫では、いま作った SIP をそのまま入力にする。
                aip_input = sip_result.sip_path if sip_result else state.input_path
                log(t("――― AIP 作成 ―――"))
                aip_result = aip_pipeline.run(
                    sip_root=aip_input,
                    output_parent=state.output_parent,
                    options=AIPOptions(
                        normalize=normalize.value,
                        serialize_zip=serialize_zip.value,
                        archivist_name=archivist.value or "",
                        descriptive=DescriptiveMetadata(
                            identifier=identifier.value or None,
                            title=title.value or None,
                            description=scope_note.value or None,
                            date=date_note.value or None,
                        ),
                    ),
                    progress=log,
                )
                show_result(aip_result)

        except (SIPPipelineError, AIPPipelineError) as exc:
            # 想定内の失敗。原因が分かる形で 1 行出す。
            _show_error(messages.pipeline_error(exc.message))
        except Exception as exc:  # noqa: BLE001
            # 想定外。詳細を出さないと現場で原因が追えない。
            _show_error(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        finally:
            def _done() -> None:
                progress_bar.visible = False
                run_button.disabled = False

            ui(_done)

    def _show_error(message: str, detail: str = "") -> None:
        """ワーカースレッドから呼ばれる。組み立ててから渡す（ui の説明を参照）。

        利用者が報告できる形にする。ファイルに記録し、画面には環境と本文を出し、
        丸ごとコピーできるようにする。書き写してもらうことは期待しない。
        """
        env = applog.environment()
        saved = applog.record(message, detail)
        report = f"{env}\n{message}" + (f"\n\n{detail}" if detail else "")

        async def _copy(_e) -> None:
            # Clipboard.set は coroutine。同期で呼ぶと何も起きないまま
            # 「awaited されなかった」警告が出るだけになる。
            await clipboard.set(report)
            log(t("エラーの内容をコピーしました。報告に貼り付けてください。"))

        box = (
            ft.Container(
                ft.Column(
                    [
                        ft.Text(t("処理を完了できませんでした"), weight=ft.FontWeight.BOLD),
                        ft.Text(message, size=12, selectable=True),
                        *(
                            [
                                ft.Text(
                                    detail, size=10, selectable=True,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                )
                            ]
                            if detail
                            else []
                        ),
                        ft.Divider(height=1),
                        ft.Text(env, size=10, selectable=True,
                                color=ft.Colors.ON_SURFACE_VARIANT),
                        *(
                            [ft.Text(t("記録: {path}", path=saved), size=10, selectable=True,
                                     color=ft.Colors.ON_SURFACE_VARIANT)]
                            if saved
                            else []
                        ),
                        ft.Row(
                            [
                                ft.OutlinedButton(
                                    t("内容をコピー"), icon=ft.Icons.CONTENT_COPY,
                                    on_click=_copy,
                                ),
                                ft.Text(
                                    t("コピーした内容を nakamura@hi.u-tokyo.ac.jp まで"
                                      "お送りいただけると助かります。"),
                                    size=10, color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                            ],
                            spacing=8,
                        ),
                    ],
                    spacing=4,
                ),
                bgcolor=ft.Colors.RED_50,
                border=ft.Border.all(1, ft.Colors.RED_400),
                border_radius=6,
                padding=10,
            )
        )
        ui(lambda: result_panel.controls.append(box))

    def on_app_error(e) -> None:
        """想定外の落ち方を拾う。

        2026-09-11 の IndexError は、こちらの try/except の外（Flet の内部）で
        起きたため、アプリは落ちたことすら記録していなかった。ここで受ける。
        """
        _show_error(t("想定外のエラーが発生しました。"), str(getattr(e, "data", e)))

    page.on_error = on_app_error

    def on_run(_e: ft.ControlEvent) -> None:
        clear_log()
        run_button.disabled = True
        progress_bar.visible = True
        page.update()
        threading.Thread(target=worker, daemon=True).start()

    run_button.on_click = on_run

    def update_virus_db_worker() -> None:
        """freshclam を別スレッドで走らせる。数百 MB のダウンロードなので。"""
        try:
            clamav.update_database(progress=log, status=log_status)
            log(t("ウイルス定義の更新が完了しました。"))
        except (SIPPipelineError, AIPPipelineError) as exc:
            _show_error(exc.message)
        except Exception as exc:  # noqa: BLE001
            _show_error(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        finally:
            def _done() -> None:
                virus_db_status.value = virus_db_message()
                virus_db_button.disabled = False
                run_button.disabled = (
                    state.input_path is None or state.output_parent is None
                )
                progress_bar.visible = False

            ui(_done)

    def on_update_virus_db(_e: ft.ControlEvent) -> None:
        clear_log()
        log(t("ウイルス定義を取得しています（数百 MB あります）…"))
        virus_db_button.disabled = True
        # 更新中に本処理を始めると、途中の DB で検査してしまう。
        run_button.disabled = True
        progress_bar.visible = True
        page.update()
        threading.Thread(target=update_virus_db_worker, daemon=True).start()

    virus_db_button.on_click = on_update_virus_db

    # ------------------------------------------------------------------
    # レイアウト
    # ------------------------------------------------------------------

    def section(heading: str, *controls: ft.Control) -> ft.Control:
        return ft.Column(
            [ft.Text(heading, weight=ft.FontWeight.BOLD, size=13), *controls],
            spacing=6,
        )

    #: オプションは既定のままで使えるので、閉じておく。ただし**閉じたまま
    #: 中身が変わると気づけない**ので、有効なものを見出しに出す。
    options_summary = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
    options_tile = ft.ExpansionTile(
        title=ft.Text(t("オプション"), weight=ft.FontWeight.BOLD, size=13),
        subtitle=options_summary,
        controls=[
            ft.Container(
                ft.Column(
                    [make_bag, sanitize, scan_pii, scan_virus, normalize, serialize_zip],
                    spacing=4,
                ),
                padding=ft.Padding.only(left=12, right=12, bottom=8),
            )
        ],
    )

    metadata_section = section(
        t("記述メタデータ"), identifier, title, date_note, scope_note, archivist
    )
    virus_section = section(t("ウイルス定義データベース"), virus_db_status, virus_db_button)

    # モードの説明。ラジオのラベルは短くせざるを得ないので、選んだものが
    # 何をするのかを 1 行添える。
    mode_note = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)

    zip_button = ft.OutlinedButton(
        t("ZIP を選ぶ"), icon=ft.Icons.ARCHIVE, on_click=choose_input_zip,
    )
    input_button = ft.OutlinedButton(
        t("フォルダを選ぶ"), icon=ft.Icons.FOLDER, on_click=choose_input_dir,
    )

    left = ft.Column(
        [
            section(t("何を作るか"), mode, mode_note),
            ft.Divider(height=1),
            section(
                t("入力"),
                ft.Row([input_button, zip_button], wrap=True),
                input_label,
                input_note,
            ),
            section(
                t("出力先"),
                ft.OutlinedButton(
                    t("フォルダを選ぶ"),
                    icon=ft.Icons.FOLDER,
                    on_click=choose_output,
                ),
                output_label,
                output_note,
            ),
            ft.Divider(height=1),
            # **オプションを記述メタデータより上に置く。** 記述メタデータは
            # 5 欄あって縦に長く、その下に置くと既定のウィンドウ（880）では
            # 画面の下で切れる。畳んだオプションは 1 行なので、ここなら必ず見える。
            # 個人情報の走査もウイルス検査も、スクロールしないと存在に気づけない
            # 状態になっていた（69 行目の高さ調整を、実行ボタンの固定が打ち消していた）。
            options_tile,
            virus_section,
            ft.Divider(height=1),
            metadata_section,
            prior_section := section(
                t("前回の受入記録（配列前後の突合）"),
                ft.OutlinedButton(
                    t("accession.csv を選ぶ"),
                    icon=ft.Icons.UPLOAD_FILE,
                    on_click=choose_prior,
                ),
                prior_label,
            ),
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    # **実行ボタンは常に見えるようにする。** 一番よく押すものが、スクロール
    # しないと押せない状態だった。フォームだけを流し、ボタンは下に留める。
    left = ft.Column(
        [
            left,
            ft.Divider(height=1),
            ft.Container(
                ft.Column([run_button, run_hint], spacing=2),
                padding=ft.Padding.only(top=4, bottom=2),
            ),
        ],
        spacing=8,
        expand=True,
    )

    right = ft.Column(
        [
            ft.Text(t("進捗"), weight=ft.FontWeight.BOLD, size=13),
            progress_bar,
            ft.Container(
                progress_log,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=6,
                expand=True,
            ),
            ft.Container(result_panel, padding=ft.Padding.only(top=6)),
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )

    # ------------------------------------------------------------------
    # モードに応じた出し分け
    # ------------------------------------------------------------------
    #
    # **関係のない項目を出さない。** 全部を常に出していたため画面が多く見え、
    # 「AIP 作成」でも ZIP の選択や受入記録の突合が並んでいた。どれが自分に
    # 関係するのかを利用者に判断させない。

    _MODE_NOTES = {
        MODE_SIP: t("素材フォルダ（または ZIP）から受入パッケージを作ります。原本は変更しません。"),
        MODE_AIP: t("既にある SIP から長期保存パッケージを作ります。記述は SIP から引き継ぎます。"),
        MODE_FULL: t("素材から受入パッケージを作り、続けて長期保存パッケージまで作ります。"),
    }

    def apply_mode(_e: ft.ControlEvent | None = None) -> None:
        selected = mode.value
        makes_sip = selected in (MODE_SIP, MODE_FULL)
        makes_aip = selected in (MODE_AIP, MODE_FULL)

        mode_note.value = _MODE_NOTES.get(selected, "")

        # 入力の意味がモードで変わる。AIP 作成の入力は「素材」ではなく SIP。
        # **Flet 0.86 のボタンの文字は content。** text に入れても何も変わらず、
        # 以前はここが効いていなかった。
        input_button.content = (
            t("SIP のフォルダを選ぶ") if selected == MODE_AIP else t("フォルダを選ぶ")
        )
        # 入力の種類が変わったら、選んであった入力を外す。素材のフォルダを
        # 選んだまま「AIP 作成」に切り替えると、そのまま実行できてしまっていた。
        kind = "sip" if selected == MODE_AIP else "material"
        if state.input_kind and kind != state.input_kind and state.input_path is not None:
            state.input_path = None
            input_label.value = t("未選択")
        state.input_kind = kind
        zip_button.visible = makes_sip          # ZIP から受け入れるのは SIP 作成のとき

        # 記述メタデータは SIP を作るときに入力する。AIP 作成では SIP から読む。
        metadata_section.visible = makes_sip
        # 配列前後の突合は、受入記録を作る側（SIP 作成）の話。
        prior_section.visible = makes_sip

        # オプションも、効く場面でだけ出す。
        make_bag.visible = makes_sip
        sanitize.visible = makes_sip
        scan_pii.visible = makes_sip
        scan_virus.visible = makes_sip
        normalize.visible = makes_aip
        virus_section.visible = makes_sip and scan_virus.value

        # 閉じたまま中身が変わると気づけないので、有効なものを見出しに出す。
        on = [c.data for c in (make_bag, sanitize, scan_pii, scan_virus,
                                normalize, serialize_zip)
              if c.visible and c.value]
        options_summary.value = t("、").join(on) if on else t("既定のまま")

        refresh_run_enabled()

        # 初回は page.add より前に呼ぶ。まだ画面に載っていないコントロールを
        # update すると RuntimeError になるので、載ってからだけ更新する。
        if state.on_page:
            left.update()

    title.on_change = lambda _e: refresh_run_enabled()
    mode.on_change = apply_mode
    for _cb in (make_bag, sanitize, scan_pii, scan_virus, normalize, serialize_zip):
        # ウイルス検査を使わないなら定義 DB の欄も要らない。見出しの更新も兼ねる。
        _cb.on_change = apply_mode
    apply_mode()

    main_view = ft.Row(
        [
            ft.Container(left, width=420, padding=16),
            ft.VerticalDivider(width=1),
            ft.Container(right, expand=True, padding=16),
        ],
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    #: 本画面とビューアを入れ替える器。ビューアはツリーと中身に幅が要るので、
    #: 並べて置かず、画面ごと切り替える（Swift 版も同じ作りだった）。
    shell = ft.Container(main_view, expand=True)

    def close_viewer() -> None:
        shell.content = main_view
        shell.update()

    def open_viewer(path: Path) -> None:
        shell.content = viewer.build(path, on_close=close_viewer)
        shell.update()

    def open_about(_e: ft.ControlEvent | None = None) -> None:
        shell.content = about.build(on_close=close_viewer)
        shell.update()

    state.open_viewer = open_viewer

    def continue_to_aip(sip_path: Path) -> None:
        mode.value = MODE_AIP
        apply_mode()
        state.input_path = sip_path
        input_label.value = str(sip_path)
        refresh_run_enabled()
        log(t("AIP 作成に切り替え、作った SIP を入力にしました。出力先を確かめて「実行」を押してください。"))
        page.update()

    state.continue_to_aip = continue_to_aip

    def on_language(_e: ft.ControlEvent) -> None:
        """表示言語を切り替える。

        **文字はコントロールを作るときに決まる**（label も text も、あとから
        全部を差し替える手立てが無い）ので、画面ごと組み立て直す。
        入力済みの内容は失われるが、言語を選ぶのは作業を始める前なので、
        個々の値を持ち回る仕掛けを足すより、作り直すほうが壊れにくい。
        """
        chosen = language.value or i18n.current_language()
        if chosen == i18n.current_language():
            return
        i18n.set_language(chosen)  # 次に起動したときも同じ言語で出す
        # 作り直す前に、前の画面が page に付けたものを外す。残したまま main を
        # 呼ぶと FilePicker とクリップボードが二重に登録される。
        page.controls.clear()
        page.services.clear()
        main(page)
        page.update()

    #: 言語の切り替え。**上端に置く。** 英語しか読めない利用者は、
    #: 日本語の画面の中からこれを探すことになるので、見出しの高さに出す。
    language = ft.Dropdown(
        value=i18n.current_language(),
        options=[ft.DropdownOption(key=code, text=name)
                 for code, name in i18n.AVAILABLE.items()],
        on_select=on_language,
        width=150,
        dense=True,
        text_size=12,
        leading_icon=ft.Icons.TRANSLATE,
        tooltip=t("表示言語"),
    )

    # ヘッダー。アプリ名と、言語の切り替えと、いつでも開ける情報ボタン。
    # **ライセンス表示は義務**なので、起動時に一度だけ見せる形にはしない。
    header = ft.Container(
        ft.Row(
            [
                ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, size=20),
                ft.Text("Archival Packager", weight=ft.FontWeight.BOLD, size=15),
                ft.Text(f"v{__version__}", size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Container(expand=True),
                language,
                ft.IconButton(
                    ft.Icons.INFO_OUTLINE,
                    tooltip=t("使い方・ライセンス・連絡先"),
                    on_click=open_about,
                ),
            ],
            spacing=8,
        ),
        padding=ft.Padding.symmetric(horizontal=16, vertical=8),
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
    )

    page.add(ft.Column([header, shell], expand=True, spacing=0))
    state.on_page = True


#: 自己診断を指示する環境変数。値に結果ファイルのパスを入れると、そこに書く。
SELF_TEST_ENV = "ARCHIVAL_PACKAGER_SELF_TEST"


def self_test(page: ft.Page) -> None:
    """**同梱した形のまま**、外から見える部分を一通り叩いて結果を出す。

    なぜ要るか
    ----------
    Python 側のテストでは届かない層がある。画面の組み立ては tests/ で
    確かめられるが、「フォルダを選ぶ」「クリップボードに入れる」は
    Flutter 側の部品が仕事をする。そこは、署名して包んだあとの
    実物を動かさないと分からない。

      - 0.1.5 の macOS 版: 署名に entitlement が足りず、フォルダ選択が
        ENTITLEMENT_NOT_FOUND で落ちた。ビルドも起動も通っていた

    そこで、包んだアプリ自身に自己診断を持たせる。

        archival-packager --self-test

    画面を組み立て、外とやり取りする手続きを順に呼び、PASS / FAIL を
    標準出力に書いて終了コードで返す。macOS は署名したあと、Windows は
    CI のビルド直後に走らせる。
    """

    #: 結果の書き出し先。包んだアプリでは標準出力が呼び出し元まで戻らないので、
    #: ファイルに書く。
    #:
    #: **置き場は記録（errors.log）と同じ場所を既定にする。** 任意のパスを
    #: 渡しても残らないことが何度もあった（macOS の TMPDIR、CI の作業
    #: ディレクトリ）。記録が書けている場所なら確実に書ける。
    #: 環境変数にパスを入れれば、そちらにも書く。
    targets = [applog.log_path().parent / "self-test.txt"]
    given = os.environ.get(SELF_TEST_ENV, "")
    if given and given != "1":
        targets.append(Path(given))

    lines: list[str] = ["自己診断モードで起動しました"]

    def emit(line: str) -> None:
        lines.append(line)
        print(line, flush=True)
        body = "\n".join(lines) + "\n"
        for target in targets:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
            except OSError as exc:
                # 書けないと「動いていない」と見分けがつかない。記録に残す。
                applog.record("自己診断の結果を書けない", f"{target}: {exc}")

    emit("開始")
    results: list[tuple[str, str]] = []

    def record(name: str, error: BaseException | None) -> None:
        result = "OK" if error is None else f"NG {error!r}"
        results.append((name, result))
        emit(f"  {result:<4} {name}")

    main(page)
    record("画面の組み立て", None)

    picker = next(
        (s for s in page.services if isinstance(s, ft.FilePicker)), None
    )
    clipboard = next(
        (s for s in page.services if isinstance(s, ft.Clipboard)), None
    )

    finished = threading.Event()

    def finish() -> None:
        """一度だけ結果を書いて終える。"""
        if finished.is_set():
            return
        finished.set()
        failed = [f"{n}: {r}" for n, r in results if r != "OK"]
        emit("自己診断: " + ("PASS" if not failed else "FAIL"))
        # 画面とダイアログを握ったままなので、ここで落とす。
        os._exit(1 if failed else 0)

    async def probe() -> None:
        # すぐ返るものから片づける。
        if clipboard is None:
            record("クリップボード", RuntimeError("Clipboard が無い"))
        else:
            try:
                await clipboard.set("archival-packager self test")
                record("クリップボード", None)
            except BaseException as exc:  # noqa: BLE001 - 何が来ても記録する
                record("クリップボード", exc)

        # フォルダ選択は最後にする。**開いたら戻ってこない。**
        # Flet 0.86 は UI と Python を同じスレッドで動かすため、ダイアログが
        # 出ている間は Python 側が進まない（asyncio の timeout も効かない）。
        # ここで見たいのは「開けたかどうか」なので、別スレッドで時間を計り、
        # 開いたまま一定時間たったら通ったものとして終える。
        # 権限が足りない場合は、開く前に例外が返る（それが拾いたいもの）。
        if picker is None:
            record("フォルダ選択", RuntimeError("FilePicker が無い"))
            finish()
            return

        def watchdog() -> None:
            time.sleep(20)
            if not finished.is_set():
                record("フォルダ選択", None)
                finish()

        threading.Thread(target=watchdog, daemon=True).start()

        try:
            await picker.get_directory_path(dialog_title="自己診断")
        except BaseException as exc:  # noqa: BLE001 - 何が来ても記録する
            record("フォルダ選択", exc)
        finish()

    page.run_task(probe)


def run() -> None:
    # 包んだアプリでは、渡したはずの引数が Flet の入口まで届かないことがある
    # （実際 macOS の .app では届かなかった）。環境変数を主、引数を従にする。
    if os.environ.get(SELF_TEST_ENV) or "--self-test" in sys.argv:
        ft.run(self_test)
        return
    ft.run(main)


if __name__ == "__main__":
    run()
