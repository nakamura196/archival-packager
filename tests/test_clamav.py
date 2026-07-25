"""ウイルス検査（ClamAV）のテスト。

固定したいのは「検出できること」より、**検査していないのに安全に見えないこと**。
定義 DB が無い・ツールが無い・証明書が無い、はどれも「検査できなかった」であって
「ウイルスが無かった」ではない。ここを取り違えると受入判断を誤らせる。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from archival_packager.core import bundled, clamav
from archival_packager.core.models import SIPPipelineError

# EICAR 標準アンチウイルステストファイル。無害だがどの製品も検出する。
# 実際のマルウェアを置かずに検出経路を通せる唯一の手段。
EICAR = (
    r"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


class TestDatabasePresence:
    def test_absent_database_is_reported_as_absent(self, tmp_path):
        assert not clamav.has_database(tmp_path)
        assert "未取得" in clamav.database_status(tmp_path)

    def test_absent_database_does_not_read_as_clean(self, tmp_path):
        """「未取得」を「検出なし」と読み違えられては困る。"""
        status = clamav.database_status(tmp_path)
        assert "検出なし" not in status
        assert "スキップ" in status

    def test_present_database_shows_when_it_was_updated(self, tmp_path):
        (tmp_path / "daily.cvd").write_bytes(b"x")
        (tmp_path / "main.cvd").write_bytes(b"x")
        status = clamav.database_status(tmp_path)
        assert clamav.has_database(tmp_path)
        assert "取得済み" in status
        assert "2 ファイル" in status

    def test_unrelated_files_do_not_count_as_a_database(self, tmp_path):
        (tmp_path / "readme.txt").write_bytes(b"x")
        assert not clamav.has_database(tmp_path)


class TestDatabaseLocation:
    def test_lives_outside_the_application(self):
        """.app や Program Files は読み取り専用。定義 DB はユーザ領域に置く。"""
        directory = clamav.database_directory()
        assert directory.is_absolute()
        assert "archival-packager" in str(directory)

    def test_follows_the_platform_convention(self, monkeypatch):
        if os.name == "nt":
            monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
            assert "AppData" in str(clamav.database_directory())
        else:
            monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg")
            assert str(clamav.database_directory()).startswith("/tmp/xdg")


class TestCertificates:
    """CVD 検証用の証明書は、探索先がビルド時の絶対パスに焼き込まれている。

    同梱して明示的に渡さないと、配布先では定義 DB を読めない。開発機に ClamAV が
    入っていると「動いてしまう」ので、siegfried の default.sig と同じく気づきにくい。
    """

    def test_environment_variable_is_used_not_just_the_flag(self, monkeypatch, tmp_path):
        # --cvdcertsdir だけでは freshclam 内部の DB 検証まで届かない。
        certs = tmp_path / "clamav-certs"
        certs.mkdir()
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        env = clamav._tool_environment()
        assert env is not None
        assert env["CVD_CERTS_DIR"] == str(certs)

    def test_no_certs_means_no_override(self, monkeypatch, tmp_path):
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        monkeypatch.setattr(clamav, "find_updater", lambda: None)
        assert clamav._tool_environment() is None

    def test_environment_is_inherited_not_replaced(self, monkeypatch, tmp_path):
        """env を差し替えるとき PATH 等を落とすと、配布先でだけ挙動が変わる。"""
        (tmp_path / "clamav-certs").mkdir()
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        monkeypatch.setenv("SOME_EXISTING_VAR", "kept")
        assert clamav._tool_environment()["SOME_EXISTING_VAR"] == "kept"


class TestMissingTools:
    def test_scan_without_clamscan_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(clamav, "find_tool", lambda: None)
        with pytest.raises(SIPPipelineError) as exc:
            clamav.scan(tmp_path)
        assert "clamscan" in exc.value.message

    def test_update_without_freshclam_raises(self, monkeypatch):
        monkeypatch.setattr(clamav, "find_updater", lambda: None)
        with pytest.raises(SIPPipelineError) as exc:
            clamav.update_database()
        assert "freshclam" in exc.value.message


class TestOutputParsing:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("/a/b.txt: Eicar-Test-Signature FOUND", ("/a/b.txt", "Eicar-Test-Signature")),
            # 資料名にコロンや空白が入るのは普通のこと。
            ("/a/報告書: 最終版.doc: Win.Test.X FOUND", ("/a/報告書: 最終版.doc", "Win.Test.X")),
        ],
    )
    def test_finding_lines(self, line, expected, monkeypatch, tmp_path):
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        monkeypatch.setattr(clamav.bundled, "ensure_executable", lambda _p: None)

        class Result:
            returncode = 1
            stdout = line + "\n"
            stderr = ""

        monkeypatch.setattr(clamav.subprocess, "run", lambda *a, **k: Result())
        assert clamav.scan(tmp_path) == {expected[0]: expected[1]}

    def test_non_finding_lines_are_ignored(self, monkeypatch, tmp_path):
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        monkeypatch.setattr(clamav.bundled, "ensure_executable", lambda _p: None)

        class Result:
            returncode = 0
            stdout = "/a/b.txt: OK\n\n----------- SCAN SUMMARY -----------\n"
            stderr = ""

        monkeypatch.setattr(clamav.subprocess, "run", lambda *a, **k: Result())
        assert clamav.scan(tmp_path) == {}

    def test_detection_is_not_treated_as_a_tool_failure(self, monkeypatch, tmp_path):
        """clamscan は検出時に終了コード 1 を返す。これは正常動作。"""
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        monkeypatch.setattr(clamav.bundled, "ensure_executable", lambda _p: None)

        class Result:
            returncode = 1
            stdout = "/a/b.txt: X FOUND\n"
            stderr = ""

        monkeypatch.setattr(clamav.subprocess, "run", lambda *a, **k: Result())
        assert clamav.scan(tmp_path)  # 例外にならない

    def test_real_error_is_raised(self, monkeypatch, tmp_path):
        monkeypatch.setattr(clamav, "find_tool", lambda: tmp_path / "clamscan")
        monkeypatch.setattr(clamav.bundled, "ensure_executable", lambda _p: None)

        class Result:
            returncode = 2
            stdout = ""
            stderr = "Broken or not a CVD file"

        monkeypatch.setattr(clamav.subprocess, "run", lambda *a, **k: Result())
        with pytest.raises(SIPPipelineError):
            clamav.scan(tmp_path)


@pytest.mark.skipif(
    bundled.find("clamscan") is None or not clamav.has_database(),
    reason="同梱 clamscan と取得済み定義 DB がある環境でのみ実行",
)
class TestRealScan:
    """同梱バイナリ・証明書・定義 DB が揃って初めて通る経路。

    ここが通らないと、配布物では「検査したつもりで何も見ていない」状態になる。
    証明書の同梱漏れはまさにこれで、clamscan は静かに 0 件を返していた。
    """

    def test_detects_the_eicar_test_signature(self, tmp_path):
        (tmp_path / "eicar.txt").write_text(EICAR, encoding="ascii")
        found = clamav.scan(tmp_path)
        assert len(found) == 1
        assert "Eicar" in next(iter(found.values()))

    def test_leaves_clean_files_alone(self, tmp_path):
        (tmp_path / "clean.txt").write_text("ただの文書です", encoding="utf-8")
        assert clamav.scan(tmp_path) == {}

    def test_finds_it_in_a_subdirectory(self, tmp_path):
        nested = tmp_path / "文書" / "sub"
        nested.mkdir(parents=True)
        (nested / "eicar.txt").write_text(EICAR, encoding="ascii")
        (tmp_path / "clean.txt").write_text("ok", encoding="utf-8")
        found = clamav.scan(tmp_path)
        assert len(found) == 1
        assert Path(next(iter(found))).name == "eicar.txt"
