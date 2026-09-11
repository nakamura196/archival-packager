"""OS ごとに違う振る舞いを、その OS の上で確かめる。

**このファイルは、走らせる OS を選ぶ。** Linux で全部通っても意味がない。
CI では Windows と macOS のジョブでも pytest を回すこと。

2026-09-11、配布した Windows 版で「ボタンを押すとコマンドプロンプトが開く」
という報告があった。原因は subprocess に CREATE_NO_WINDOW を渡していなかった
こと。**pytest を Linux でしか回していなかったため、誰も気づけなかった。**
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from archival_packager.core import bundled, siegfried


class TestNoConsoleWindow:
    """外部ツールの起動でコンソール窓を出さないこと。"""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows でのみ意味がある")
    def test_flag_is_set_on_windows(self):
        kwargs = bundled.no_window()
        assert kwargs.get("creationflags") == subprocess.CREATE_NO_WINDOW, (
            "Windows では CREATE_NO_WINDOW を渡すこと。"
            "渡さないと外部ツールを起動するたびに黒い窓が開く"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows 以外でのみ意味がある")
    def test_flag_is_absent_elsewhere(self):
        """macOS / Linux に CREATE_NO_WINDOW は存在しない。渡すと落ちる。"""
        assert bundled.no_window() == {}

    @pytest.mark.skipif(
        bundled.find("sf") is None, reason="同梱 sf が無い環境"
    )
    def test_identify_still_works_with_the_flag(self, tmp_path: Path):
        """窓を抑える指定を足したあとも、実際に識別できること。

        フラグの渡し方を間違えると OSError で落ちる。存在チェックだけでは
        気づけないので、実際に 1 回動かす。
        """
        (tmp_path / "sample.txt").write_text("hello", encoding="utf-8")
        results = siegfried.identify(tmp_path)
        assert results, "siegfried が何も返さない"


class TestBundledToolsResolveOnThisOS:
    """同梱ツールの探索が、この OS の実行ファイル名で解決できること。

    Windows は .exe が付く。macOS は付かない。解決に失敗すると
    「同梱していない」と判定され、検査が黙ってスキップされる。
    """

    @pytest.mark.skipif(
        bundled.bin_dir() is None, reason="同梱ディレクトリが無い環境"
    )
    def test_siegfried_is_found(self):
        assert bundled.find("sf") is not None, (
            f"この OS（{sys.platform}）で sf を解決できない。"
            "実行ファイル名の付け方を確認すること"
        )

    @pytest.mark.skipif(
        bundled.bin_dir() is None, reason="同梱ディレクトリが無い環境"
    )
    def test_signature_database_is_next_to_the_tool(self):
        """default.sig が無いと、配布先でユーザ領域を見に行って FATAL で落ちる。"""
        assert bundled.signature_home() is not None, (
            "default.sig が同梱ディレクトリに無い"
        )


class TestUserDataLocation:
    """利用者ごとのデータの置き場所が、その OS の作法に沿っていること。

    インストール先へ書き込むと、MSIX（インストール先が読み取り専用）で
    動かなくなる。
    """

    def test_log_is_outside_the_install_directory(self):
        from archival_packager.core import applog

        path = applog.log_path()
        install_dir = Path(sys.executable).resolve().parent
        assert install_dir not in path.resolve().parents, (
            "ログをインストール先に書いている。MSIX では書き込めない"
        )
        if sys.platform == "win32":
            assert "AppData" in str(path) or "LOCALAPPDATA" in str(path)

    def test_virus_database_is_outside_the_install_directory(self):
        from archival_packager.core import clamav

        path = clamav.database_directory()
        install_dir = Path(sys.executable).resolve().parent
        assert install_dir not in path.resolve().parents


class TestNaiveTimestampsDoNotCrash:
    """tz を持たない日時でも落ちないこと。

    Windows では、素の datetime を astimezone でローカル時刻へ直そうとすると
    1970-01-01 付近で OSError になる。CI の Windows で実際に踏んだ。
    """

    def _file(self):
        from datetime import datetime
        from pathlib import Path

        from archival_packager.core.models import ScannedFile

        return ScannedFile(
            relative_path="a.txt",
            absolute_path=Path("/x/a.txt"),
            size_bytes=1,
            modified=datetime(1970, 1, 1, 0, 0, 0),
        )

    def test_description_sheet(self):
        from archival_packager.core import spreadsheets

        assert "1970-01-01T00:00:00Z" in spreadsheets.formats([self._file()])

    def test_epoch_timestamp_from_the_filesystem(self):
        """更新日時が 1970-01-01 のファイルがあっても処理が止まらないこと。"""
        from datetime import datetime

        from archival_packager.core import spreadsheets

        f = self._file()
        object.__setattr__(f, "modified", datetime.fromtimestamp(0))
        assert spreadsheets.formats([f])

    def test_dfxml_and_sheet_agree(self):
        """同じ日時が、技術メタデータと記述シートで同じ文字列になること。"""
        from archival_packager.core import dfxml, spreadsheets

        assert dfxml._iso(self._file().modified) == spreadsheets._iso(
            self._file().modified
        )
