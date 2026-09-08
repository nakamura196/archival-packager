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

import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from ..core import aip_pipeline, clamav, sip_pipeline
from ..core.aip_models import AIPOptions, AIPPipelineError, AIPResult, DescriptiveMetadata
from ..core.models import SIPMetadata, SIPOptions, SIPPipelineError, SIPResult
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


def main(page: ft.Page) -> None:
    page.title = "Archival Packager"
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
    result_panel = ft.Column(spacing=8)

    def log(message: str) -> None:
        progress_log.controls.append(ft.Text(message, size=12, selectable=True))
        page.update()

    def clear_log() -> None:
        progress_log.controls.clear()
        result_panel.controls.clear()
        page.update()

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
    title = ft.TextField(label="タイトル", hint_text="例: 総務課 一般文書", dense=True)
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

    def refresh_run_enabled() -> None:
        run_button.disabled = state.input_path is None or state.output_parent is None
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
        """成果物と、目視確認が必要な点を出す。"""
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

        result_panel.controls.append(
            ft.Text(headline, weight=ft.FontWeight.BOLD, size=15)
        )
        result_panel.controls.append(
            ft.Row(
                [
                    ft.Text(str(path), size=12, selectable=True, expand=True),
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
            result_panel.controls.append(
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
            result_panel.controls.append(
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
            result_panel.controls.append(
                ft.Text("目視確認が必要な点はありません。", size=12, color=ft.Colors.GREEN_700)
            )

        page.update()

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
            progress_bar.visible = False
            run_button.disabled = False
            page.update()

    def _show_error(message: str, detail: str = "") -> None:
        result_panel.controls.append(
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
                    ],
                    spacing=4,
                ),
                bgcolor=ft.Colors.RED_50,
                border=ft.Border.all(1, ft.Colors.RED_400),
                border_radius=6,
                padding=10,
            )
        )
        page.update()

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
            clamav.update_database(progress=log)
            log("ウイルス定義の更新が完了しました。")
        except (SIPPipelineError, AIPPipelineError) as exc:
            _show_error(exc.message)
        except Exception as exc:  # noqa: BLE001
            _show_error(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        finally:
            virus_db_status.value = virus_db_message()
            virus_db_button.disabled = False
            run_button.disabled = state.input_path is None or state.output_parent is None
            progress_bar.visible = False
            page.update()

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

    left = ft.Column(
        [
            section("何を作るか", mode),
            ft.Divider(height=1),
            section(
                "入力",
                ft.Row(
                    [
                        ft.OutlinedButton(
                            "フォルダを選ぶ",
                            icon=ft.Icons.FOLDER,
                            on_click=choose_input_dir,
                        ),
                        ft.OutlinedButton(
                            "ZIP を選ぶ",
                            icon=ft.Icons.ARCHIVE,
                            on_click=choose_input_zip,
                        ),
                    ],
                    wrap=True,
                ),
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
            section("記述メタデータ", identifier, title, date_note, scope_note, archivist),
            ft.Divider(height=1),
            section(
                "オプション",
                make_bag, sanitize, scan_pii, scan_virus, normalize, serialize_zip,
            ),
            section("ウイルス定義データベース", virus_db_status, virus_db_button),
            section(
                "前回の受入記録（配列前後の突合）",
                ft.OutlinedButton(
                    "accession.csv を選ぶ",
                    icon=ft.Icons.UPLOAD_FILE,
                    on_click=choose_prior,
                ),
                prior_label,
            ),
            ft.Container(run_button, padding=ft.Padding.only(top=8)),
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
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

    page.add(
        ft.Row(
            [
                ft.Container(left, width=420, padding=16),
                ft.VerticalDivider(width=1),
                ft.Container(right, expand=True, padding=16),
            ],
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
    )


def run() -> None:
    ft.run(main)


if __name__ == "__main__":
    run()
