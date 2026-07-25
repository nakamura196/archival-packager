"""UI の結線テスト。

画面の見た目は自動テストの対象にしない（人が見て判断すべきもの）。
代わりに「壊れると気づきにくく、壊れると致命的な結線」だけを固定する。

  - パイプラインを別スレッドで走らせているか（UI スレッドで走らせると固まる）
  - 例外を握り潰していないか（原因が分からないまま「失敗しました」だけ出るのが最悪）
  - 結果に出すパスが実在するか
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
