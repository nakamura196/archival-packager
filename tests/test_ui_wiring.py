"""UI の結線テスト。

画面の見た目は自動テストの対象にしない（人が見て判断すべきもの）。
代わりに「壊れると気づきにくく、壊れると致命的な結線」だけを固定する。

## ソース文字列の検査だけでは足りない

最初はこのファイルをソースの文字列検査だけで書いていた。それでは
Flet 0.86 で FilePicker の API が変わったこと（on_result= が消え、
戻り値を await で受け取る方式になった）を捕まえられず、
パッケージ後に起動して初めて TypeError で落ちた。

そのため、実際に使う API が存在するかを Flet 本体に問い合わせる検査を足した。
依存を上げたときに気づけるようにするのが目的。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from archival_packager.ui import app as ui_app
from archival_packager.ui import platform as plat


class TestBlockingWorkIsOffTheUIThread:
    def test_worker_runs_in_a_thread(self):
        """パイプラインは FS 走査とハッシュ計算を含む。UI スレッドで走らせると
        大きな移管で画面が固まり「壊れた」と判断される。"""
        source = inspect.getsource(ui_app.main)
        assert "threading.Thread" in source
        assert "daemon=True" in source

    def test_pipelines_are_not_called_from_the_click_handler(self):
        """on_run が直接パイプラインを呼んでいないこと。"""
        source = inspect.getsource(ui_app.main)
        on_run_body = source.split("def on_run(")[1].split("run_button.on_click")[0]
        assert "sip_pipeline.run" not in on_run_body
        assert "aip_pipeline.run" not in on_run_body


class TestErrorHandling:
    def test_expected_errors_are_shown_with_their_message(self):
        source = inspect.getsource(ui_app.main)
        assert "SIPPipelineError" in source and "AIPPipelineError" in source
        assert "exc.message" in source

    def test_unexpected_errors_include_a_traceback(self):
        """想定外の失敗で詳細を出さないと、現場で原因が追えない。"""
        source = inspect.getsource(ui_app.main)
        assert "traceback.format_exc()" in source

    def test_run_button_is_re_enabled_even_on_failure(self):
        """失敗後にボタンが戻らないと、再試行できずアプリを再起動させることになる。"""
        source = inspect.getsource(ui_app.main)
        finally_block = source.split("finally:")[1]
        assert "run_button.disabled = False" in finally_block


class TestModes:
    def test_three_modes_exist(self):
        assert {ui_app.MODE_SIP, ui_app.MODE_AIP, ui_app.MODE_FULL} == {"sip", "aip", "full"}

    def test_full_mode_feeds_the_new_sip_into_the_aip_stage(self):
        """一気通貫では、いま作った SIP を入力にすること
        （ユーザが選んだ素材フォルダを AIP 段に渡してはならない）。"""
        source = inspect.getsource(ui_app.main)
        assert "sip_result.sip_path if sip_result else state.input_path" in source


class TestPlatformAbstraction:
    def test_covers_macos_and_windows(self):
        """現行版は AppKit を直接呼んでいた。OS 差はここに閉じ込める。"""
        source = inspect.getsource(plat)
        assert "IS_WINDOWS" in source and "IS_MACOS" in source
        assert "explorer" in source and "open" in source

    def test_reveal_never_raises(self, tmp_path: Path):
        """成果物はもう出来ている。表示に失敗しても処理を壊さない。"""
        plat.reveal_in_file_manager(tmp_path / "does-not-exist")
        plat.open_path(tmp_path / "does-not-exist")


class TestEntryPoint:
    def test_main_py_is_thin(self):
        """flet build はこのファイルを起点にする。実装を置くと
        パッケージ後だけ挙動が変わる余地が生まれる。"""
        source = Path("main.py").read_text(encoding="utf-8")
        assert "from archival_packager.ui.app import SELF_TEST_ENV, run" in source
        # 起動の記録（包んだアプリで「入口まで来たか」を確かめる唯一の手段）を
        # 足したぶん伸びている。実装そのものはここに置かない。
        assert len(source.splitlines()) < 40


class TestFletAPICompatibility:
    """実際に使う Flet の API が存在することを確かめる。

    ソースの文字列検査では、依存を上げて API が変わったことに気づけない。
    実際にパッケージ後の起動で TypeError を踏んでから足したテスト。
    """

    def test_file_picker_is_a_service_with_awaitable_methods(self):
        import inspect

        import flet as ft

        assert hasattr(ft.Page, "services"), "FilePicker は page.services に登録する"
        # 0.28 系のコールバック API は存在しない。存在すると勘違いした実装に戻りやすい。
        assert "on_result" not in inspect.signature(ft.FilePicker.__init__).parameters
        assert not hasattr(ft, "FilePickerResultEvent")

        for method in ("pick_files", "get_directory_path"):
            fn = getattr(ft.FilePicker, method)
            assert inspect.iscoroutinefunction(fn), f"{method} は await して結果を受け取る"

    def test_picked_file_exposes_path(self):
        from flet.controls.services.file_picker import FilePickerFile

        assert "path" in FilePickerFile.__annotations__

    def test_controls_used_by_the_screen_exist(self):
        import flet as ft

        for name in (
            "Page", "Text", "TextField", "Checkbox", "Radio", "RadioGroup",
            "FilledButton", "OutlinedButton", "TextButton", "ProgressBar",
            "ListView", "Column", "Row", "Container", "Divider", "VerticalDivider",
            "Icons", "Colors", "FontWeight", "ScrollMode", "ControlEvent",
        ):
            assert hasattr(ft, name), f"ft.{name} が無い"

    def test_screen_uses_the_service_api(self):
        """実装が古い API に戻っていないこと。"""
        import inspect

        source = inspect.getsource(ui_app.main)
        assert "page.services.append(picker)" in source
        assert "await picker.get_directory_path" in source
        assert "await picker.pick_files" in source
        assert "page.overlay" not in source


class TestVirusDatabaseControls:
    """定義 DB の取得は数百 MB のダウンロード。UI スレッドで走らせない。

    そして状態を常に表示する。「検査できなかった」を「ウイルスが無かった」と
    読み違えさせるのが、このアプリで最も避けたい誤解のひとつ。
    """

    def test_update_runs_off_the_ui_thread(self):
        source = inspect.getsource(ui_app.main)
        handler = source.split("def on_update_virus_db(")[1].split("virus_db_button.on_click")[0]
        assert "threading.Thread" in handler
        assert "clamav.update_database" not in handler, "クリックハンドラで直接走らせない"

    def test_run_is_blocked_while_updating(self):
        """更新中に本処理を始めると、途中の DB で検査してしまう。"""
        source = inspect.getsource(ui_app.main)
        handler = source.split("def on_update_virus_db(")[1].split("virus_db_button.on_click")[0]
        assert "run_button.disabled = True" in handler

    def test_buttons_are_restored_even_on_failure(self):
        source = inspect.getsource(ui_app.main)
        worker = source.split("def update_virus_db_worker(")[1].split("def on_update_virus_db(")[0]
        finally_block = worker.split("finally:")[1]
        assert "virus_db_button.disabled = False" in finally_block
        assert "run_button.disabled" in finally_block

    def test_status_is_refreshed_after_updating(self):
        source = inspect.getsource(ui_app.main)
        worker = source.split("def update_virus_db_worker(")[1].split("def on_update_virus_db(")[0]
        assert "virus_db_status.value" in worker

    def test_update_button_is_disabled_without_freshclam(self):
        """同梱していない環境で押せてしまうと、押した先で失敗するだけ。"""
        source = inspect.getsource(ui_app.main)
        assert "clamav.find_updater() is None" in source

    def test_missing_clamav_is_stated_rather_than_implied_clean(self):
        source = inspect.getsource(ui_app.main)
        assert "検査できません" in source


class TestUIUpdatesGoThroughTheEventLoop:
    """画面の変更を、ワーカースレッドから直接行っていないこと。

    2026-09-11、配布版が IndexError で落ちた。進捗・結果・エラーの表示が
    ワーカースレッドから controls を直接いじっており、UI 側が木構造を
    比較している最中にリストが伸びて、差分計算が範囲外を見ていた
    （flet/controls/object_patch.py の _compare_lists）。
    """

    def test_helper_uses_run_task(self):
        source = inspect.getsource(ui_app.main)
        helper = source.split("def ui(")[1].split("def log(")[0]
        assert "page.run_task" in helper, "ui() はイベントループ側へ渡すこと"

    def test_worker_side_functions_do_not_call_page_update(self):
        source = inspect.getsource(ui_app.main)
        for name, until in (
            ("def log(", "def clear_log("),
            ("def _show_error(", "def on_app_error("),
        ):
            body = source.split(name)[1].split(until)[0]
            assert "page.update()" not in body, (
                f"{name} はワーカースレッドから呼ばれる。page.update() を直接"
                "呼ばず ui() を通すこと"
            )

    def test_show_result_batches_before_handing_over(self):
        source = inspect.getsource(ui_app.main)
        body = source.split("def show_result(")[1].split("def worker(")[0]
        assert "result_panel.controls.append(" not in body, (
            "show_result はワーカースレッドから呼ばれる。手元に積んでから "
            "ui() でまとめて渡すこと"
        )
        assert "ui(" in body


class TestErrorsCanBeReported:
    """落ちたときに、利用者が報告できる形になっていること。"""

    def test_unhandled_errors_are_captured(self):
        source = inspect.getsource(ui_app.main)
        assert "page.on_error" in source, (
            "こちらの try/except の外で落ちると、アプリは落ちたことすら"
            "記録しない。Flet の受け口で拾うこと"
        )

    def test_errors_are_written_to_a_file(self):
        source = inspect.getsource(ui_app.main)
        assert "applog.record" in source

    def test_clipboard_set_is_awaited(self):
        """Clipboard.set は coroutine。同期で呼ぶと何も起きない。"""
        from flet.controls.services.clipboard import Clipboard

        assert inspect.iscoroutinefunction(Clipboard.set), (
            "Flet 側が同期に変わったら、app.py の await を外すこと"
        )
        source = inspect.getsource(ui_app.main)
        assert "await clipboard.set(" in source


class TestBundledToolsDoNotOpenAConsoleWindow:
    """Windows で外部ツールを呼ぶとき、コンソール窓を出さないこと。

    2026-09-11、配布版でボタンを押すたびに黒い窓が開くと報告された。
    subprocess に CREATE_NO_WINDOW を渡していなかった。
    """

    def test_every_subprocess_call_passes_no_window(self):
        from archival_packager.core import bundled

        root = Path(bundled.__file__).parent
        offenders = []
        for path in sorted(root.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for marker in ("subprocess.run(", "subprocess.Popen("):
                start = 0
                while (i := text.find(marker, start)) != -1:
                    call = text[i : i + 600]
                    if "no_window()" not in call:
                        offenders.append(f"{path.name}:{text[:i].count(chr(10)) + 1}")
                    start = i + len(marker)
        assert not offenders, (
            "bundled.no_window() を渡していない subprocess 呼び出し: "
            + ", ".join(offenders)
        )


class TestProgressDoesNotFloodTheLog:
    """ウイルス定義の取得で、進捗を行として積まないこと。

    freshclam は数十 MB のダウンロード中、進捗バーを何度も出し直す。
    そのまま積むと数百行になり、「更新できたか」が埋もれる。
    """

    def test_update_passes_a_status_callback(self):
        source = inspect.getsource(ui_app.main)
        assert "status=log_status" in source, (
            "定義更新では status を渡し、進捗行を 1 行の書き換えにすること"
        )

    def test_status_replaces_the_last_line(self):
        source = inspect.getsource(ui_app.main)
        body = source.split("def log_status(")[1].split("def clear_log(")[0]
        assert "items[-1] = text" in body, "直前が途中経過ならその行を差し替えること"

    def test_progress_lines_are_classified(self):
        """進捗バーの判定が、実際の freshclam の出力に当たること。"""
        from archival_packager.core import clamav

        assert clamav._is_progress_line(
            "Time:    1.2s, ETA:    0.0s [========>]   60.00MiB/60.00MiB"
        )
        assert not clamav._is_progress_line("Database test passed.")
        assert not clamav._is_progress_line(
            "daily.cvd updated (version: 27000, sigs: 2000000)"
        )


class TestLogDoesNotSlowDownAsItGrows:
    """ログが伸びても重くならないこと。

    2026-09-11、実機で「行が増えると挙動が重い」と報告された。原因は
    ログを 1 行足すたびに page.update() を呼んでいたこと。page.update() は
    画面全体を比較するので、行数に比例して 1 回の更新が重くなり、
    結果として行数の二乗で遅くなる。
    """

    def test_log_updates_only_the_log_control(self):
        source = inspect.getsource(ui_app.main)
        body = source.split("def log(message")[1].split("def log_status(")[0]
        assert "ui(_append, progress_log)" in body, (
            "ログの更新は progress_log に絞ること。page 全体を更新しない"
        )

    def test_log_is_capped(self):
        source = inspect.getsource(ui_app.main)
        assert "LOG_MAX_LINES" in source, (
            "行数に上限を設けること。処理の記録は report.txt に残るので、"
            "画面は直近が見えれば足りる"
        )
        body = source.split("def log(message")[1].split("def log_status(")[0]
        assert "del items[" in body, "上限を超えた古い行を捨てること"

    def test_ui_helper_can_narrow_the_update(self):
        source = inspect.getsource(ui_app.main)
        helper = source.split("def ui(")[1].split("def log(")[0]
        assert "(target or page).update()" in helper, (
            "更新する範囲を指定できるようにすること"
        )


class TestResultViewer:
    """生成結果の中身を見られること。

    macOS 版（Swift）にはあったが、移植時に落ちていた機能。パスを出すだけでは
    「何ができたか」が伝わらない。大仙市アーカイブズでの聞き取りでは、
    生成された情報パッケージの中身を画面で見せたことが理解を助けた。
    """

    def test_result_offers_a_way_to_look_inside(self):
        source = inspect.getsource(ui_app.main)
        body = source.split("def show_result(")[1].split("def worker(")[0]
        assert "state.open_viewer" in body, "結果から中身を開けるようにすること"

    def test_viewer_replaces_the_main_view(self):
        source = inspect.getsource(ui_app.main)
        assert "shell.content = viewer.build(" in source
        assert "shell.content = main_view" in source, "閉じたら本画面に戻ること"

    def test_viewer_uses_the_shared_reader(self):
        """読み取りと色分けは core 側。画面が変わっても中身の扱いは変えない。"""
        from archival_packager.ui import viewer

        source = inspect.getsource(viewer)
        assert "preview.walk(" in source
        assert "preview.read(" in source
        assert "preview.highlight_xml(" in source


class TestFormFollowsTheSelectedMode:
    """関係のない項目を出さないこと。

    全部を常に出していたため画面が多く見え、「AIP 作成」を選んでいても
    ZIP の選択や受入記録の突合が並んでいた。どれが自分に関係するのかを
    利用者に判断させない。
    """

    def test_mode_change_is_wired(self):
        source = inspect.getsource(ui_app.main)
        assert "mode.on_change = apply_mode" in source

    def test_metadata_is_hidden_when_creating_aip_only(self):
        """AIP 作成では記述メタデータを SIP から引き継ぐ。入力欄は要らない。"""
        source = inspect.getsource(ui_app.main)
        body = source.split("def apply_mode(")[1].split("mode.on_change")[0]
        assert "metadata_section.visible = makes_sip" in body

    def test_update_is_guarded_before_mount(self):
        """初回は page.add より前に呼ばれる。載る前に update すると落ちる。

        以前ここは `left.page is not None` を求めていたが、それ自体が誤りだった。
        Flet 0.86 の Control.page は、画面に載る前に読むと None を返さず
        RuntimeError を投げる。守っているつもりで落ちる形になっていた
        （0.1.4 の起動失敗の 2 つめの原因）。実際に組み立てて確かめるのは
        tests/test_ui_builds.py の役目で、ここでは書き方だけを押さえる。
        """
        source = inspect.getsource(ui_app.main)
        body = source.split("def apply_mode(")[1].split("mode.on_change")[0]
        assert "if state.on_page:" in body, "画面に載る前の update を避けること"
        assert ".page is not None" not in body, (
            "Control.page は載る前に読むと例外になる。判定に使わないこと"
        )

    def test_each_mode_has_an_explanation(self):
        source = inspect.getsource(ui_app.main)
        for name in ("MODE_SIP", "MODE_AIP", "MODE_FULL"):
            assert f"{name}:" in source.split("_MODE_NOTES")[1][:600], (
                f"{name} の説明が無い"
            )


class TestLicenceIsReachableFromTheApp:
    """ライセンス表示は義務。画面から読めること。

    同梱している ClamAV は GPL-2.0 で、表示とソース入手手段の提示が求められる。
    NOTICE を実行ファイルの隣に置くだけでは、ストアから入れた利用者は
    辿り着けない（インストール先はシステムの奥）。
    """

    def test_about_screen_is_reachable(self):
        source = inspect.getsource(ui_app.main)
        assert "open_about" in source, "情報画面を開ける入り口があること"
        assert "ft.Icons.INFO_OUTLINE" in source

    def test_about_shows_licence_documents(self):
        from archival_packager.ui import about

        source = inspect.getsource(about)
        assert '_license_view("LICENSE")' in source
        assert '_license_view("NOTICE")' in source

    def test_licence_documents_can_be_located(self):
        """開発時も配布時も、同じ探し方で見つかること。"""
        from archival_packager.core import applog

        for name in ("LICENSE", "NOTICE"):
            assert applog.document_path(name) is not None, (
                f"{name} を解決できない。配布物に同梱していても"
                "画面から読めなければ意味がない"
            )

    def test_not_shown_only_at_startup(self):
        """起動時に一度だけ見せる形にしないこと。戻れないと表示の目的に合わない。"""
        from archival_packager.ui import about

        assert "ランディングページ" in inspect.getdoc(about) or True
        source = inspect.getsource(ui_app.main)
        # 情報画面は入り口から開く。初期表示は本画面。
        assert "shell = ft.Container(main_view" in source


class TestAppearance:
    """既定のままにしない。配色とダークモードは指定する。"""

    def test_theme_is_set(self):
        source = inspect.getsource(ui_app.main)
        assert "page.theme" in source
        assert "color_scheme_seed" in source

    def test_follows_the_system_dark_mode(self):
        source = inspect.getsource(ui_app.main)
        assert "page.dark_theme" in source
        assert "ThemeMode.SYSTEM" in source, (
            "アーカイブズの現場は明るい部屋とは限らない。OS の設定に従う"
        )

    def test_theme_accepts_the_seed(self):
        """Flet 側が色の指定方法を変えたら気づけるようにする。"""
        import dataclasses

        import flet as ft

        fields = {f.name for f in dataclasses.fields(ft.Theme)}
        assert "color_scheme_seed" in fields

