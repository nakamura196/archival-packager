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
        assert "from archival_packager.ui.app import run" in source
        assert len(source.splitlines()) < 30


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
