"""Archival Packager の画面。

現行 Swift 版の SwiftUI 画面（ArchivalPackagerApp / FullRootView / ContentView /
AIPContentView / ResultViewer）に対応する。

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

from ..core.aip_models import AIPOptions, AIPPipelineError, AIPResult, DescriptiveMetadata
from .. import __version__
from ..core import aip_pipeline, applog, clamav, sip_pipeline
from ..core.models import SIPMetadata, SIPOptions, SIPPipelineError, SIPResult
from . import about, platform as plat, viewer

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
    run_button = ft.FilledButton("実行", icon=ft.Icons.PLAY_ARROW, disabled=True)
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
                ft.Radio(value=MODE_SIP, label="SIP 作成（素材フォルダ／ZIP から受入パッケージ）"),
                ft.Radio(value=MODE_AIP, label="AIP 作成（SIP から長期保存パッケージ）"),
                ft.Radio(value=MODE_FULL, label="素材から AIP まで一気通貫"),
            ],
            spacing=2,
        ),
    )

    # ------------------------------------------------------------------
    # 記述メタデータ
    # ------------------------------------------------------------------

    identifier = ft.TextField(label="識別子", hint_text="例: 2026-移管-総務課", dense=True)
    title = ft.TextField(
        label="タイトル（必須）", hint_text="例: 総務課 一般文書", dense=True
    )
    scope_note = ft.TextField(label="内容・範囲", multiline=True, min_lines=2, max_lines=4, dense=True)
    date_note = ft.TextField(label="年代", hint_text="例: 2024–2025", dense=True)
    archivist = ft.TextField(
        label="担当者名",
        hint_text="PREMIS に保存処理の実施者として記録されます",
        dense=True,
    )

    # ------------------------------------------------------------------
    # オプション
    # ------------------------------------------------------------------

    make_bag = ft.Checkbox(label="BagIt bag として梱包する", value=False)
    scan_pii = ft.Checkbox(label="個人情報(PII)を走査する", value=False)
    scan_virus = ft.Checkbox(label="ウイルス検査を行う（定義 DB が必要）", value=False)
    sanitize = ft.Checkbox(
        label="ファイル名を安全化する（元名は accession.csv に残ります）", value=False
    )
    serialize_zip = ft.Checkbox(label="成果物を ZIP（無圧縮）に固める", value=False)
    normalize = ft.Checkbox(label="保存用フォーマットへ変換する（AIP）", value=True)

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
            return "ウイルス定義: ClamAV が同梱されていないため検査できません"
        return clamav.database_status()

    virus_db_status = ft.Text(
        virus_db_message(), size=12, color=ft.Colors.ON_SURFACE_VARIANT
    )
    virus_db_button = ft.OutlinedButton(
        "定義を取得 / 更新",
        icon=ft.Icons.CLOUD_DOWNLOAD,
        disabled=clamav.find_updater() is None,
    )

    # ------------------------------------------------------------------
    # ファイル選択
    # ------------------------------------------------------------------

    input_label = ft.Text("未選択", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    output_label = ft.Text("未選択", size=12, color=ft.Colors.ON_SURFACE_VARIANT)
    prior_label = ft.Text("未選択（任意）", size=12, color=ft.Colors.ON_SURFACE_VARIANT)

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
            missing.append("素材フォルダ" if mode.value != MODE_AIP else "SIP フォルダ")
        if state.output_parent is None:
            missing.append("出力先")
        if needs_title and not (title.value or "").strip():
            missing.append("タイトル")
        run_button.disabled = bool(missing)
        run_hint.value = ("あと " + "・".join(missing) + " を指定すると押せます"
                          if missing else "")
        run_hint.visible = bool(missing)
        # 組み立ての途中（page.add より前）にも呼ばれる。まだ画面が無いうちは
        # 送らない。
        if state.on_page:
            page.update()

    async def choose_input_dir(_e: ft.ControlEvent) -> None:
        chosen = await picker.get_directory_path(dialog_title="素材フォルダ / SIP を選ぶ")
        if chosen:
            state.input_path = Path(chosen)
            input_label.value = chosen
        refresh_run_enabled()

    async def choose_input_zip(_e: ft.ControlEvent) -> None:
        files = await picker.pick_files(
            dialog_title="受入 ZIP を選ぶ", allowed_extensions=["zip"], allow_multiple=False
        )
        if files:
            state.input_path = Path(files[0].path)
            input_label.value = files[0].path
        refresh_run_enabled()

    async def choose_output(_e: ft.ControlEvent) -> None:
        chosen = await picker.get_directory_path(dialog_title="出力先フォルダを選ぶ")
        if chosen:
            state.output_parent = Path(chosen)
            output_label.value = chosen
        refresh_run_enabled()

    async def choose_prior(_e: ft.ControlEvent) -> None:
        files = await picker.pick_files(
            dialog_title="前回の accession.csv を選ぶ",
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
            headline = f"SIP を作成しました（{result.file_count} 件 / {result.total_bytes:,} バイト）"
            extras = [
                ("記述スプレッドシート", result.spreadsheet_path),
                ("レポート", result.report_path),
                ("PII レポート", result.pii_report_path),
                ("配列前後の対応表", result.arrangement_map_path),
                ("ZIP", result.zip_path),
            ]
        else:
            path = result.aip_path
            headline = (
                f"AIP を作成しました（原本 {result.original_count} 件 / "
                f"派生物 {result.derivative_count} 件）"
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
                        "中身を見る",
                        icon=ft.Icons.FIND_IN_PAGE_OUTLINED,
                        on_click=lambda _e, p=path: state.open_viewer(p),
                    ),
                    ft.OutlinedButton(
                        "場所を開く",
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
                            "開く", on_click=lambda _e, p=candidate: plat.open_path(p)
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
                                f"目視確認が必要な点: {len(result.warnings)} 件",
                                weight=ft.FontWeight.BOLD,
                            ),
                            *[ft.Text(f"・{w}", size=12) for w in result.warnings[:50]],
                            *(
                                [ft.Text(f"（他 {len(result.warnings) - 50} 件）", size=12)]
                                if len(result.warnings) > 50
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
                ft.Text("目視確認が必要な点はありません。", size=12, color=ft.Colors.GREEN_700)
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
                log("――― AIP 作成 ―――")
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
            _show_error(exc.message)
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
            log("エラーの内容をコピーしました。報告に貼り付けてください。")

        box = (
            ft.Container(
                ft.Column(
                    [
                        ft.Text("処理を完了できませんでした", weight=ft.FontWeight.BOLD),
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
                            [ft.Text(f"記録: {saved}", size=10, selectable=True,
                                     color=ft.Colors.ON_SURFACE_VARIANT)]
                            if saved
                            else []
                        ),
                        ft.Row(
                            [
                                ft.OutlinedButton(
                                    "内容をコピー", icon=ft.Icons.CONTENT_COPY,
                                    on_click=_copy,
                                ),
                                ft.Text(
                                    "コピーした内容を nakamura@hi.u-tokyo.ac.jp まで"
                                    "お送りいただけると助かります。",
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
        _show_error("想定外のエラーが発生しました。", str(getattr(e, "data", e)))

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
            log("ウイルス定義の更新が完了しました。")
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
        log("ウイルス定義を取得しています（数百 MB あります）…")
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
        title=ft.Text("オプション", weight=ft.FontWeight.BOLD, size=13),
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
        "記述メタデータ", identifier, title, date_note, scope_note, archivist
    )
    virus_section = section("ウイルス定義データベース", virus_db_status, virus_db_button)

    # モードの説明。ラジオのラベルは短くせざるを得ないので、選んだものが
    # 何をするのかを 1 行添える。
    mode_note = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)

    zip_button = ft.OutlinedButton(
        "ZIP を選ぶ", icon=ft.Icons.ARCHIVE, on_click=choose_input_zip,
    )
    input_button = ft.OutlinedButton(
        "フォルダを選ぶ", icon=ft.Icons.FOLDER, on_click=choose_input_dir,
    )

    left = ft.Column(
        [
            section("何を作るか", mode, mode_note),
            ft.Divider(height=1),
            section(
                "入力",
                ft.Row([input_button, zip_button], wrap=True),
                input_label,
            ),
            section(
                "出力先",
                ft.OutlinedButton(
                    "フォルダを選ぶ",
                    icon=ft.Icons.FOLDER,
                    on_click=choose_output,
                ),
                output_label,
            ),
            ft.Divider(height=1),
            metadata_section,
            ft.Divider(height=1),
            options_tile,
            virus_section,
            prior_section := section(
                "前回の受入記録（配列前後の突合）",
                ft.OutlinedButton(
                    "accession.csv を選ぶ",
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
            ft.Text("進捗", weight=ft.FontWeight.BOLD, size=13),
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
        MODE_SIP: "素材フォルダ（または ZIP）から受入パッケージを作ります。原本は変更しません。",
        MODE_AIP: "既にある SIP から長期保存パッケージを作ります。記述は SIP から引き継ぎます。",
        MODE_FULL: "素材から受入パッケージを作り、続けて長期保存パッケージまで作ります。",
    }

    def apply_mode(_e: ft.ControlEvent | None = None) -> None:
        selected = mode.value
        makes_sip = selected in (MODE_SIP, MODE_FULL)
        makes_aip = selected in (MODE_AIP, MODE_FULL)

        mode_note.value = _MODE_NOTES.get(selected, "")

        # 入力の意味がモードで変わる。AIP 作成の入力は「素材」ではなく SIP。
        input_button.text = "SIP のフォルダを選ぶ" if selected == MODE_AIP else "フォルダを選ぶ"
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
        on = [c.label for c in (make_bag, sanitize, scan_pii, scan_virus,
                                normalize, serialize_zip)
              if c.visible and c.value]
        options_summary.value = "、".join(on) if on else "既定のまま"

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
    #: 並べて置かず、画面ごと切り替える（Swift 版も同じ作り）。
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

    # ヘッダー。アプリ名と、いつでも開ける情報ボタン。
    # **ライセンス表示は義務**なので、起動時に一度だけ見せる形にはしない。
    header = ft.Container(
        ft.Row(
            [
                ft.Icon(ft.Icons.INVENTORY_2_OUTLINED, size=20),
                ft.Text("Archival Packager", weight=ft.FontWeight.BOLD, size=15),
                ft.Text(f"v{__version__}", size=11,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Container(expand=True),
                ft.IconButton(
                    ft.Icons.INFO_OUTLINE,
                    tooltip="使い方・ライセンス・連絡先",
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
    import asyncio

    #: 結果の書き出し先。包んだアプリでは標準出力が呼び出し元まで戻らないので、
    #: ファイルに書く。環境変数にパスを入れて指示する（"1" ならファイルなし）。
    report_path = os.environ.get(SELF_TEST_ENV, "")
    lines: list[str] = ["自己診断モードで起動しました"]

    def emit(line: str) -> None:
        lines.append(line)
        print(line, flush=True)
        if report_path and report_path != "1":
            try:
                Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError as exc:
                # 書けないと「動いていない」と見分けがつかない。記録に残す。
                applog.record("自己診断の結果を書けない", f"{report_path}: {exc}")

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
