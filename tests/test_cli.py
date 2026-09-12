"""コマンドライン入口のテスト。

固定したいのは、**自動処理に組み込んだときに人を欺かないこと**。
CLI は cron や CI から呼ばれる前提で、画面と違って誰も見ていない。
そこで次の 4 点を守らせる。

  1. 標準出力と標準エラーの役割を混ぜない（``--json`` がパイプで使える）
  2. 終了コードの意味を変えない（0 成功 / 1 処理の失敗 / 2 引数の誤り）
  3. **ウイルスや個人情報の検出で終了コードを 1 にしない。**
     検出は人が判断する材料であって処理の失敗ではない。ただし黙らない
  4. 原本を書き換えない（このアプリの最重要の性質）

CLI は薄い層なので、パイプラインそのものの正しさは test_sip_pipeline.py /
test_aip_pipeline.py が見ている。ここは境界だけを見る。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from archival_packager import cli
from archival_packager.core import clamav


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    (src / "文書").mkdir(parents=True)
    (src / "a.txt").write_text("これは資料です。連絡先 taro@example.com\n", encoding="utf-8")
    (src / "文書" / "b.txt").write_text("普通の本文\n", encoding="utf-8")
    return src


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    path = tmp_path / "out"
    path.mkdir()
    return path


def sip_argv(source: Path, out_dir: Path, *extra: str) -> list[str]:
    return [
        "sip",
        "--input", str(source),
        "--output", str(out_dir),
        "--identifier", "2026-移管-総務課",
        "--title", "総務課文書",
        *extra,
    ]


def snapshot(root: Path) -> dict[str, tuple[int, str]]:
    """配下のファイルを (サイズ, SHA-256) で写し取る。"""
    out: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            out[str(path.relative_to(root))] = (len(data), hashlib.sha256(data).hexdigest())
    return out


class TestSIP:
    def test_creates_a_sip(self, source, out_dir, capsys):
        """通しで動くこと。ここが通らなければ他の細かい約束に意味がない。"""
        code = cli.main(sip_argv(source, out_dir))
        assert code == 0

        package = out_dir / "2026-移管-総務課"
        assert (package / "objects" / "a.txt").is_file()
        assert (package / "objects" / "文書" / "b.txt").is_file()
        subdoc = package / "metadata" / "submissionDocumentation"
        for name in ("description.csv", "formats.csv", "report.txt"):
            assert (subdoc / name).is_file(), name

        out = capsys.readouterr().out
        assert str(package) in out, "どこに出来たかを標準出力で伝えること"

    def test_bag_option_reaches_the_pipeline(self, source, out_dir):
        """オプションが素通りしていないこと（薄い層でも配線は間違える）。"""
        assert cli.main(sip_argv(source, out_dir, "--bag")) == 0
        assert (out_dir / "2026-移管-総務課" / "bagit.txt").is_file()

    def test_originals_are_never_modified(self, source, out_dir):
        """**原本を書き換えない。** このアプリが手放してはいけない性質。

        画面側は tests/ で守られているが、CLI は別の入口。入口ごとに
        確かめないと、片方だけ入力へ書くようになっても誰も気づかない。
        """
        before = snapshot(source)
        assert cli.main(sip_argv(source, out_dir, "--scan-pii", "--sanitize-filenames")) == 0
        assert snapshot(source) == before


class TestJSON:
    def test_stdout_is_exactly_one_json_document(self, source, out_dir, capsys):
        """``--json`` の標準出力に進捗を混ぜないこと。

        混ざると ``| jq`` で落ちる。自動処理での主な使い方がそれなので、
        ここが崩れると CLI を足した意味がなくなる。
        """
        assert cli.main(sip_argv(source, out_dir, "--json")) == 0
        captured = capsys.readouterr()

        payload = json.loads(captured.out)  # 進捗が 1 行でも混ざればここで落ちる
        assert payload["status"] == "ok"
        assert payload["file_count"] == 2
        assert payload["sip_path"] == str(out_dir / "2026-移管-総務課")
        assert "入力フォルダを走査しています…" not in captured.out
        assert "入力フォルダを走査しています…" in captured.err, "進捗は標準エラーへ"

    def test_quiet_keeps_the_result(self, source, out_dir, capsys):
        """``--quiet`` が止めるのは進捗だけ。結果まで消えては使えない。"""
        assert cli.main(sip_argv(source, out_dir, "--json", "--quiet")) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["status"] == "ok"
        assert "走査しています" not in captured.err

    def test_failure_is_also_one_json_document(self, tmp_path, out_dir, capsys):
        """失敗も JSON 1 個で返す。パイプの先で成否を分岐できるようにするため。"""
        code = cli.main(
            ["sip", "--input", str(tmp_path / "無い"), "--output", str(out_dir),
             "--identifier", "x", "--title", "y", "--json"]
        )
        assert code == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "error"
        assert "見つかりません" in payload["message"]


class TestArguments:
    def test_missing_arguments_say_what_is_missing(self, capsys):
        """終了コード 2 と、**何が足りないか**が読める日本語。

        argparse の既定は英語。「the following arguments are required」では、
        画面しか使ったことのない担当者には何を直せばよいか分からない。
        """
        assert cli.main(["sip"]) == 2
        err = capsys.readouterr().err
        assert "--input" in err and "--identifier" in err
        assert "足りません" in err
        assert "例:" in err, "直し方まで出すこと"

    def test_unknown_option_is_an_argument_error(self, capsys):
        assert cli.main(["sip", "--存在しない"]) == 2
        assert "知らない引数" in capsys.readouterr().err

    def test_missing_input_directory_is_readable(self, tmp_path, out_dir, capsys):
        """入力が無いときは、パスと確認すべきことを出して止まること。"""
        code = cli.main(
            ["sip", "--input", str(tmp_path / "どこにも無い"), "--output", str(out_dir),
             "--identifier", "x", "--title", "y"]
        )
        assert code == 2
        err = capsys.readouterr().err
        assert "どこにも無い" in err
        assert "確認してください" in err

    def test_output_parent_must_exist(self, source, tmp_path, capsys):
        """親ごと作らない。綴りを間違えたパスに黙って書くと成果物を見失う。"""
        code = cli.main(sip_argv(source, tmp_path / "無い親" / "子"))
        assert code == 2
        assert "親フォルダ" in capsys.readouterr().err

    def test_no_command_shows_help(self, capsys):
        assert cli.main([]) == 2
        assert "sip" in capsys.readouterr().err


class TestFindings:
    """検出があっても処理は成功。ただし黙らない。"""

    @pytest.fixture
    def infected(self, monkeypatch, source):
        """ClamAV が 1 件検出した状態を作る（実物のウイルスは置けない）。"""
        monkeypatch.setattr(clamav, "find_tool", lambda: Path("/fake/clamscan"))
        monkeypatch.setattr(clamav, "has_database", lambda *_a, **_k: True)
        monkeypatch.setattr(
            clamav, "scan", lambda *_a, **_k: {str(source / "a.txt"): "Eicar-Test-Signature"}
        )
        return source

    def test_virus_detection_still_exits_zero(self, infected, out_dir, capsys):
        """**検出で終了コードを 1 にしない。**

        感染の有無は人が判断すること。ここを失敗にすると、毎晩の自動処理が
        「止めるべき失敗」と「見てほしい所見」を区別できなくなる。
        """
        code = cli.main(sip_argv(infected, out_dir, "--virus-scan", "--json"))
        assert code == 0

        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["status"] == "ok"
        assert any("Eicar" in hit for hit in payload["findings"]["virus"])
        assert "Eicar" in captured.err, "終了コードが 0 なので、標準エラーに出さないと気づけない"
        assert "確認が必要" in captured.err

    def test_virus_detection_is_not_silenced_by_quiet(self, infected, out_dir, capsys):
        """``--quiet`` は進捗を止めるだけ。検出まで黙らせてはならない。"""
        assert cli.main(sip_argv(infected, out_dir, "--virus-scan", "--quiet")) == 0
        assert "Eicar" in capsys.readouterr().err

    def test_pii_candidates_still_exit_zero(self, source, out_dir, capsys):
        assert cli.main(sip_argv(source, out_dir, "--scan-pii", "--json")) == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["findings"]["pii"], "候補を結果に残すこと"
        assert "確認が必要" in captured.err

    def test_warning_prefixes_match_the_core(self, source, out_dir, capsys):
        """警告の前置きで検出を分けている、という結びつきを固定する。

        SIPResult は件数を持たず、手元にあるのは警告の文字列だけ。
        sip_builder.collect_warnings の前置きが変わると CLI の分類が
        静かに空になるので、ここで気づけるようにしておく。
        """
        from archival_packager.core.sip_builder import collect_warnings

        assert cli.main(sip_argv(source, out_dir, "--scan-pii", "--json")) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["warnings"], "前提: このデータでは警告が出る"
        # collect_warnings が実際に使っている前置きだけで分類できていること。
        classified = sum(len(v) for v in payload["findings"].values())
        assert classified == len(payload["warnings"]), (
            f"分類できない警告がある。collect_warnings の前置きを確認すること: "
            f"{collect_warnings.__module__}"
        )


class TestAIP:
    def test_creates_an_aip_from_a_sip(self, source, out_dir, tmp_path, capsys):
        """SIP → AIP を CLI だけで繋げられること（自動化の肝）。"""
        assert cli.main(sip_argv(source, out_dir)) == 0
        capsys.readouterr()

        aip_out = tmp_path / "aip"
        aip_out.mkdir()
        code = cli.main(
            ["aip", "--sip", str(out_dir / "2026-移管-総務課"), "--output", str(aip_out),
             "--json", "--quiet"]
        )
        assert code == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["original_count"] == 2
        assert payload["fixity"]["outcome"] == "passed"
        assert Path(payload["mets_path"]).is_file()
        assert (Path(payload["aip_path"]) / "bagit.txt").is_file()

    def test_rejects_a_folder_that_is_not_a_sip(self, tmp_path, capsys):
        """SIP でないフォルダは、**何を渡すべきか**を言って失敗すること。"""
        plain = tmp_path / "ただのフォルダ"
        plain.mkdir()
        aip_out = tmp_path / "aip"
        aip_out.mkdir()
        code = cli.main(["aip", "--sip", str(plain), "--output", str(aip_out)])
        assert code == 1, "引数の書き方ではなく中身の問題なので 1"
        assert "objects/" in capsys.readouterr().err


class TestInspect:
    def test_shows_what_is_inside_a_sip(self, source, out_dir, capsys):
        assert cli.main(sip_argv(source, out_dir)) == 0
        capsys.readouterr()

        assert cli.main(["inspect", str(out_dir / "2026-移管-総務課"), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["overview"]["kind"] == "SIP"
        assert payload["overview"]["identifier"] == "2026-移管-総務課"
        assert {f["path"] for f in payload["files"]} == {"a.txt", "文書/b.txt"}

    def test_unreadable_package_fails_with_advice(self, tmp_path, capsys):
        plain = tmp_path / "ただのフォルダ"
        plain.mkdir()
        assert cli.main(["inspect", str(plain)]) == 1
        assert "読めませんでした" in capsys.readouterr().err


class TestCheck:
    def test_reports_the_state_of_the_tools(self, capsys):
        """道具が無くても失敗にしない（ソースから動かす環境では普通のこと）。

        ここを 1 にすると CI の最初の 1 行が常に赤くなり、誰も読まなくなる。
        """
        assert cli.main(["check", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["version"]
        assert "virus_database" in payload
        assert set(payload["bundled_tools"]) == {
            "siegfried", "siegfried_signature", "clamscan", "freshclam"
        }

    def test_does_not_claim_a_missing_database_is_clean(self, monkeypatch, tmp_path, capsys):
        """「定義 DB が無い」を「ウイルスなし」と読ませないこと。"""
        monkeypatch.setattr(clamav, "database_directory", lambda: tmp_path / "空")
        assert cli.main(["check"]) == 0
        out = capsys.readouterr().out
        assert "未取得" in out
        assert "検出なし" not in out
