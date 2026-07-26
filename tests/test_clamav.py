"""ウイルス検査（ClamAV）のテスト。

固定したいのは「検出できること」より、**検査していないのに安全に見えないこと**。
定義 DB が無い・ツールが無い・証明書が無い、はどれも「検査できなかった」であって
「ウイルスが無かった」ではない。ここを取り違えると受入判断を誤らせる。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from archival_packager.core import bundled, clamav
from archival_packager.core.models import SIPPipelineError


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
            ("/a/b.txt: Test.Local.Signature FOUND", ("/a/b.txt", "Test.Local.Signature")),
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
    bundled.find("clamscan") is None,
    reason="同梱 clamscan がある環境でのみ実行",
)
class TestRealScan:
    """同梱バイナリと証明書が揃って初めて通る経路。

    ここが通らないと、配布物では「検査したつもりで何も見ていない」状態になる。
    証明書の同梱漏れはまさにこれで、clamscan は定義 DB を読めないまま
    エラー終了していた。

    ## EICAR テストシグネチャは使わない

    検出経路の検証には EICAR（無害だがどの製品も検出する標準テスト文字列）を
    使うのが定石だが、ここでは使わない。**開発機の端末保護ソフトが反応し、
    組織のセキュリティ担当へ通報が飛ぶ**ため。テストのために人を動かすのは割に合わない。

    代わりに、無害なテキストファイルの SHA-256 を自作シグネチャ(.hsb)にして
    自分で「検出」させる。マルウェアらしき内容が一切ディスクに載らない上に、
    検証できる範囲はむしろ広い:

      - clamscan が起動し、指定したディレクトリから定義を読めること
      - 証明書の配線ができていること（無いと定義の読み込み自体が code 2 で失敗する）
      - 検出行 "<path>: <signature> FOUND" を正しく解析できること
      - 検出しなかったファイルを巻き込まないこと

    定義 DB 全体（3.6M シグネチャ）を読まないので実行も速い。
    """

    @staticmethod
    def _signature_for(target: Path, db_dir: Path, name: str = "Test.Local.Signature") -> None:
        """target の内容ハッシュを検出条件にした .hsb を db_dir に書く。

        書式は "<SHA-256>:<バイト数>:<シグネチャ名>"。ClamAV はハッシュ長で
        アルゴリズムを判別する。
        """
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        db_dir.mkdir(parents=True, exist_ok=True)
        (db_dir / "local.hsb").write_text(
            f"{digest}:{len(data)}:{name}\n", encoding="ascii"
        )

    def test_detects_a_file_matching_the_loaded_signatures(self, tmp_path):
        target = tmp_path / "in" / "対象.txt"
        target.parent.mkdir()
        target.write_text("これはただの文書です", encoding="utf-8")
        self._signature_for(target, tmp_path / "db")

        found = clamav.scan(tmp_path / "in", database=tmp_path / "db")
        assert len(found) == 1
        assert Path(next(iter(found))).name == "対象.txt"
        assert "Test.Local.Signature" in next(iter(found.values()))

    def test_leaves_other_files_alone(self, tmp_path):
        target = tmp_path / "in" / "対象.txt"
        target.parent.mkdir()
        target.write_text("これはただの文書です", encoding="utf-8")
        (target.parent / "無関係.txt").write_text("別の内容", encoding="utf-8")
        self._signature_for(target, tmp_path / "db")

        found = clamav.scan(tmp_path / "in", database=tmp_path / "db")
        assert [Path(p).name for p in found] == ["対象.txt"]

    def test_recurses_into_subdirectories(self, tmp_path):
        nested = tmp_path / "in" / "文書" / "sub"
        nested.mkdir(parents=True)
        target = nested / "対象.txt"
        target.write_text("これはただの文書です", encoding="utf-8")
        self._signature_for(target, tmp_path / "db")

        found = clamav.scan(tmp_path / "in", database=tmp_path / "db")
        assert len(found) == 1

    def test_nothing_is_reported_when_no_signature_matches(self, tmp_path):
        target = tmp_path / "in" / "対象.txt"
        target.parent.mkdir()
        target.write_text("これはただの文書です", encoding="utf-8")
        self._signature_for(target, tmp_path / "db")
        # 署名を作った後で中身を変える。もう一致しない。
        target.write_text("書き換えました", encoding="utf-8")

        assert clamav.scan(tmp_path / "in", database=tmp_path / "db") == {}

    def test_certificates_are_wired_up(self, tmp_path):
        """証明書が無いと定義の読み込み自体が失敗する（code 2 → 例外）。

        この配線が抜けていると clamscan はエラー終了する。それを握り潰すと
        「検査したが何も出なかった」と読める結果になってしまう。
        """
        target = tmp_path / "in" / "対象.txt"
        target.parent.mkdir()
        target.write_text("これはただの文書です", encoding="utf-8")
        self._signature_for(target, tmp_path / "db")

        assert clamav.certs_directory() is not None, "証明書が同梱されていない"
        clamav.scan(tmp_path / "in", database=tmp_path / "db")  # 例外にならない
