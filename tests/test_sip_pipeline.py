"""SIP パイプラインの結合テスト（入力フォルダから SIP まで通す）。

個々のモジュールは単体テストで固定してあるので、ここは「繋がっていること」と
「実際に開ける成果物が出ること」を見る。
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import bundled, clamav, report, sip_pipeline, zip_io
from archival_packager.core import scan as scan_mod
from archival_packager.core.models import (
    ScannedFile,
    SIPMetadata,
    SIPOptions,
    SIPPipelineError,
)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    (src / "文書" / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("これは資料です。連絡先 taro@example.com\n", encoding="utf-8")
    (src / "文書" / "b.txt").write_text("普通の本文\n", encoding="utf-8")
    (src / "文書" / "sub" / "c.bin").write_bytes(b"\x00\x01\x02binary")
    (src / ".DS_Store").write_bytes(b"junk")
    return src


def run(source: Path, tmp_path: Path, **opts) -> tuple:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    messages: list[str] = []
    result = sip_pipeline.run(
        input_path=source,
        output_parent=out,
        metadata=SIPMetadata(identifier="2026-移管-総務課", title="総務課文書", date_note="2024–2025"),
        options=SIPOptions(**opts),
        progress=messages.append,
    )
    return result, messages


class TestEndToEnd:
    def test_produces_a_complete_sip(self, source, tmp_path):
        result, _ = run(source, tmp_path)
        pkg = result.sip_path

        assert result.file_count == 3, ".DS_Store は数えない"
        assert (pkg / "objects" / "a.txt").is_file()
        assert (pkg / "objects" / "文書" / "sub" / "c.bin").is_file()
        assert not (pkg / "objects" / ".DS_Store").exists()

        subdoc = pkg / "metadata" / "submissionDocumentation"
        for name in ("description.csv", "formats.csv", "accession.csv",
                     "dfxml.xml", "report.txt", "report.html", "checksum.sha256"):
            assert (subdoc / name).is_file(), name

    def test_checksums_are_real(self, source, tmp_path):
        """マニフェストのハッシュが実ファイルと一致すること。"""
        result, _ = run(source, tmp_path)
        subdoc = result.sip_path / "metadata" / "submissionDocumentation"
        manifest = (subdoc / "checksum.sha256").read_text(encoding="utf-8")

        from archival_packager.core.checksums import sha256_of

        lines = [ln for ln in manifest.splitlines() if ln.strip()]
        assert len(lines) == 3
        for line in lines:
            digest, _, rel = line.partition("  ")
            assert sha256_of(result.sip_path / rel) == digest

    def test_dfxml_is_valid_xml(self, source, tmp_path):
        result, _ = run(source, tmp_path)
        path = result.sip_path / "metadata" / "submissionDocumentation" / "dfxml.xml"
        root = etree.fromstring(path.read_bytes())
        assert len(root.findall("fileobject")) == 3

    def test_progress_messages_reach_the_caller(self, source, tmp_path):
        _result, messages = run(source, tmp_path)
        joined = "\n".join(messages)
        assert "対象ファイル: 3 件" in joined
        assert "完了しました。" in joined

    def test_bag_output_validates(self, source, tmp_path):
        from archival_packager.core import sip_builder

        result, _ = run(source, tmp_path, make_bag=True)
        assert result.bagged
        sip_builder.validate_bag(result.sip_path)

    def test_empty_input_is_rejected(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(SIPPipelineError):
            run(empty, tmp_path)


class TestOptions:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows では \":\" や \"?\" を含むファイルを作れない",
    )
    def test_sanitize_records_original_names(self, tmp_path):
        """Windows で使えない文字を含む名前を、安全な名前に直して元名を残す。

        この場面自体は現実にある（macOS/Linux で作られた資料が Windows の
        アーカイブズへ持ち込まれる）。ただし **Windows 上では検証用のファイルを
        作れない**ため、そこでは飛ばす。Windows での同等の確認は、ZIP 取り込み
        経由で行うのが筋（未実装）。
        """
        src = tmp_path / "in"
        src.mkdir()
        (src / "a:b?.txt").write_text("x", encoding="utf-8")

        result, messages = run(src, tmp_path, sanitize_filenames=True)
        assert (result.sip_path / "objects" / "a_b_.txt").is_file()

        accession = (
            result.sip_path / "metadata" / "submissionDocumentation" / "accession.csv"
        ).read_text(encoding="utf-8").lstrip("﻿")
        rows = list(csv.reader(io.StringIO(accession, newline="")))
        assert rows[1][0] == "a:b?.txt", "受入時の元名を残す"
        assert "1 件" in "\n".join(messages)

    def test_pii_report_written_when_findings_exist(self, source, tmp_path):
        result, messages = run(source, tmp_path, scan_pii=True)
        assert result.pii_report_path is not None
        content = result.pii_report_path.read_text(encoding="utf-8")
        assert "メールアドレス" in content
        assert "taro@example.com" not in content, "生値を書かない"
        assert "PII 候補" in "\n".join(messages)

    def test_pii_report_absent_when_disabled(self, source, tmp_path):
        result, _ = run(source, tmp_path, scan_pii=False)
        assert result.pii_report_path is None

    def test_serialize_zip_produces_an_openable_archive(self, source, tmp_path):
        result, _ = run(source, tmp_path, serialize_zip=True)
        assert result.zip_path is not None and result.zip_path.is_file()
        with zipfile.ZipFile(result.zip_path) as zf:
            names = zf.namelist()
            assert any(n.endswith("objects/a.txt") for n in names)
            # 無圧縮であること（既に圧縮済みの資料で時間を掛けない方針）。
            assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())

    def test_arrangement_map_matches_across_rearrangement(self, tmp_path):
        """受入後にフォルダを並べ替えても、ハッシュで追跡できること。"""
        first = tmp_path / "first"
        first.mkdir()
        (first / "a.txt").write_text("payload", encoding="utf-8")
        result1, _ = run(first, tmp_path)
        prior = result1.sip_path / "metadata" / "submissionDocumentation" / "accession.csv"

        second = tmp_path / "second"
        (second / "2024" / "整理後").mkdir(parents=True)
        (second / "2024" / "整理後" / "a.txt").write_text("payload", encoding="utf-8")

        out2 = tmp_path / "out2"
        out2.mkdir()
        result2 = sip_pipeline.run(
            input_path=second,
            output_parent=out2,
            metadata=SIPMetadata(identifier="second", title="second"),
            options=SIPOptions(prior_accession_path=prior),
            progress=lambda _m: None,
        )
        assert result2.arrangement_map_path is not None
        text = result2.arrangement_map_path.read_text(encoding="utf-8")
        assert "a.txt" in text and "2024/整理後/a.txt" in text
        assert "一致" in text


class TestStructuredInput:
    def test_objects_dir_is_respected(self, tmp_path):
        """Archivematica transfer 構造をそのまま受け取れること。"""
        src = tmp_path / "transfer"
        (src / "objects" / "d").mkdir(parents=True)
        (src / "objects" / "a.txt").write_text("x", encoding="utf-8")
        (src / "objects" / "d" / "b.txt").write_text("y", encoding="utf-8")

        result, messages = run(src, tmp_path)
        assert result.file_count == 2
        assert (result.sip_path / "objects" / "a.txt").is_file()
        assert not (result.sip_path / "objects" / "objects").exists(), "二重にならない"
        assert "objects/ をそのまま尊重" in "\n".join(messages)

    def test_provided_metadata_csv_is_inherited(self, tmp_path):
        """担当者が記入済みの metadata.csv を空テンプレートで上書きしないこと。"""
        src = tmp_path / "transfer"
        (src / "objects").mkdir(parents=True)
        (src / "objects" / "a.txt").write_text("x", encoding="utf-8")
        (src / "metadata").mkdir()
        (src / "metadata" / "metadata.csv").write_text(
            "filename,dc.title\r\nobjects/a.txt,記入済みタイトル\r\n", encoding="utf-8"
        )

        result, messages = run(src, tmp_path)
        written = (result.sip_path / "metadata" / "metadata.csv").read_text(encoding="utf-8")
        assert "記入済みタイトル" in written
        assert "継承します" in "\n".join(messages)


class TestZipInput:
    def test_zip_input_is_extracted_and_recorded(self, source, tmp_path):
        archive = tmp_path / "transfer.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            for p in sorted(source.rglob("*")):
                if p.is_file():
                    zf.write(p, Path("transfer") / p.relative_to(source))

        result, messages = run(archive, tmp_path)
        assert result.file_count == 3
        report_text = (
            result.sip_path / "metadata" / "submissionDocumentation" / "report.txt"
        ).read_text(encoding="utf-8")
        # 展開は「変換」なので、元の姿を記録しないと後から検証できない。
        assert "受入元 ZIP: transfer.zip" in report_text
        assert "SHA-256:" in report_text
        assert "展開しています" in "\n".join(messages)

    def test_zip_slip_is_rejected(self, tmp_path):
        """展開先の外を指すエントリを黙って飛ばさず、明示的に失敗させる。"""
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../escaped.txt", "pwned")

        with pytest.raises(SIPPipelineError) as exc:
            zip_io.extract(archive, tmp_path / "dest")
        assert "展開先の外" in exc.value.message

    def test_single_wrapped_directory_is_unwrapped(self, tmp_path):
        """zip の中身が 1 ディレクトリで包まれている場合、余計に 1 段深くしない。"""
        extracted = tmp_path / "ex"
        (extracted / "wrapper" / "d").mkdir(parents=True)
        assert zip_io.effective_root(extracted) == extracted / "wrapper"

    def test_multiple_top_level_entries_stay_as_is(self, tmp_path):
        extracted = tmp_path / "ex"
        (extracted / "a").mkdir(parents=True)
        (extracted / "b").mkdir()
        assert zip_io.effective_root(extracted) == extracted


class TestFormatIdentificationFailure:
    """識別できないことと、作業が止まることは別。

    siegfried が配布する mac ビルドは 1 つだけで中身は arm64。一方こちらの
    アプリ本体は universal なので Intel Mac でも起動し、そこで sf の実行だけが
    失敗する。フォーマット識別は SIP の必須要素ではない（PUID 欄が空になるだけ）
    ので、移管作業そのものを落としてはいけない。
    """

    def test_unrunnable_siegfried_does_not_stop_the_sip(self, source, tmp_path, monkeypatch):
        from archival_packager.core import bundled, siegfried

        monkeypatch.setattr(bundled, "find", lambda name: Path("/nonexistent/sf"))

        def boom(*_a, **_k):
            raise OSError(8, "Exec format error")

        monkeypatch.setattr(siegfried, "identify", boom)

        result, messages = run(source, tmp_path)
        assert result.sip_path.is_dir(), "SIP は作られる"
        assert any("識別に失敗" in m for m in messages)

    def test_reported_failure_mentions_the_cause(self, source, tmp_path, monkeypatch):
        from archival_packager.core import bundled, siegfried
        from archival_packager.core.models import SIPPipelineError

        monkeypatch.setattr(bundled, "find", lambda name: Path("/nonexistent/sf"))
        monkeypatch.setattr(
            siegfried, "identify",
            lambda *_a, **_k: (_ for _ in ()).throw(
                SIPPipelineError.tool_failed("siegfried", 1, "署名 DB が壊れています")
            ),
        )
        _result, messages = run(source, tmp_path)
        assert any("署名 DB" in m for m in messages), "原因が追える形で残す"


class TestVirusScanning:
    def test_skipped_without_tool(self, source, tmp_path, monkeypatch):
        """検査できないことと、検査して検出なしだったことを混同しないこと。"""
        monkeypatch.setattr(clamav, "find_tool", lambda: None)
        result, messages = run(source, tmp_path, scan_virus=True)
        text = (
            result.sip_path / "metadata" / "submissionDocumentation" / "report.txt"
        ).read_text(encoding="utf-8")
        assert "スキップ（ClamAV 未同梱）" in text
        assert "検出なし" not in text

    def test_reports_not_run_when_disabled(self, source, tmp_path):
        result, _ = run(source, tmp_path, scan_virus=False)
        text = (
            result.sip_path / "metadata" / "submissionDocumentation" / "report.txt"
        ).read_text(encoding="utf-8")
        assert "未実施（オプション OFF）" in text


class TestScanning:
    def test_symlinks_are_not_followed(self, tmp_path):
        """辿ると入力ツリー外のファイルを受入対象にしてしまう。"""
        src = tmp_path / "in"
        src.mkdir()
        (src / "real.txt").write_text("x", encoding="utf-8")
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (src / "link.txt").symlink_to(outside)

        files = scan_mod.scan(src)
        assert [f.relative_path for f in files] == ["real.txt"]

    def test_order_is_deterministic(self, tmp_path):
        src = tmp_path / "in"
        src.mkdir()
        for name in ("c.txt", "a.txt", "b.txt"):
            (src / name).write_text("x", encoding="utf-8")
        assert [f.relative_path for f in scan_mod.scan(src)] == ["a.txt", "b.txt", "c.txt"]


class TestReportContents:
    def test_flags_points_needing_review(self):
        files = [
            ScannedFile("u.bin", Path("/x/u.bin"), 1, __import__("datetime").datetime(2026, 7, 25)),
            ScannedFile("m.txt", Path("/x/m.txt"), 1, __import__("datetime").datetime(2026, 7, 25),
                        puid="fmt/1", format_warning="extension mismatch"),
        ]
        text = report.text_report(
            files, SIPMetadata("id", "t"), SIPOptions(), virus_status="未実施（オプション OFF）"
        )
        assert "未識別: 1 件" in text
        assert "拡張子不一致: 1 件" in text

    def test_html_escapes_hostile_names(self):
        files = [
            ScannedFile("<script>.txt", Path("/x/a"), 1, __import__("datetime").datetime(2026, 7, 25))
        ]
        out = report.html_report(
            files, SIPMetadata("id", "A & B"), SIPOptions(), virus_status="未実施"
        )
        assert "<script>.txt" not in out
        assert "&lt;script&gt;.txt" in out
        assert "A &amp; B" in out

    def test_html_says_so_when_nothing_needs_review(self):
        import datetime

        files = [
            ScannedFile("ok.txt", Path("/x/ok.txt"), 1, datetime.datetime(2026, 7, 25), puid="fmt/1")
        ]
        out = report.html_report(files, SIPMetadata("id", "t"), SIPOptions(), virus_status="未実施")
        assert "目視確認が必要な点はありません" in out


class TestReportSaysWhyFormatsAreUnidentified:
    """「未識別 54 件」とだけ書かない。

    同梱ツールを持たないビルドで走らせると、全ファイルが未識別になる。
    レポートがその理由を書いていないと、資料のほうに問題があるように読める
    （実際にそう受け取られた）。ツールが無かったのか、識別した結果
    分からなかったのかで、次にやることが違う。
    """

    def test_says_the_tool_was_missing(self, tmp_path, monkeypatch):
        from archival_packager.core import bundled, sip_pipeline
        from archival_packager.core.models import SIPMetadata, SIPOptions

        monkeypatch.setattr(
            bundled, "find", lambda name: None if name == "sf" else bundled.find(name)
        )

        src = tmp_path / "in"
        src.mkdir()
        (src / "a.txt").write_text("A\n", encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()

        result = sip_pipeline.run(
            input_path=src, output_parent=out,
            metadata=SIPMetadata(identifier="x", title="t"),
            options=SIPOptions(), progress=lambda _m: None,
        )
        report = (result.sip_path / "metadata" / "submissionDocumentation"
                  / "report.txt").read_text(encoding="utf-8")
        assert "フォーマット識別:" in report
        assert "未同梱" in report, "ツールが無かったことが書かれていない"

    @pytest.mark.skipif(
        bundled.find("sf") is None, reason="同梱 sf が無い環境では識別が走らない"
    )
    def test_says_it_ran_when_it_ran(self, tmp_path):
        from archival_packager.core import sip_pipeline
        from archival_packager.core.models import SIPMetadata, SIPOptions

        src = tmp_path / "in"
        src.mkdir()
        (src / "a.txt").write_text("A\n", encoding="utf-8")
        out = tmp_path / "out"
        out.mkdir()

        result = sip_pipeline.run(
            input_path=src, output_parent=out,
            metadata=SIPMetadata(identifier="x", title="t"),
            options=SIPOptions(), progress=lambda _m: None,
        )
        report = (result.sip_path / "metadata" / "submissionDocumentation"
                  / "report.txt").read_text(encoding="utf-8")
        assert "フォーマット識別: 実施" in report


class TestUnreadableDocumentsAreNotReportedAsClean:
    """読めなかった文書を「候補なし」に混ぜない。

    壊れた PDF・暗号化された PDF はテキストを取り出せない。黙って飛ばすと、
    個人情報が入っていても report には「個人情報(PII)スキャン: 実施（候補なし）」
    とだけ出る。**利用者はこれを「安全だと確認済み」と読む。**
    走査できなかった件数を必ず添える。
    """

    def _run(self, tmp_path, make_file):
        from archival_packager.core import sip_pipeline
        from archival_packager.core.models import SIPMetadata, SIPOptions

        src = tmp_path / "in"
        src.mkdir()
        make_file(src)
        out = tmp_path / "out"
        out.mkdir()

        result = sip_pipeline.run(
            input_path=src, output_parent=out,
            metadata=SIPMetadata(identifier="x", title="t"),
            options=SIPOptions(scan_pii=True), progress=lambda _m: None,
        )
        return result, (result.sip_path / "metadata" / "submissionDocumentation"
                        / "report.txt").read_text(encoding="utf-8")

    def test_broken_pdf_is_counted_as_unscanned(self, tmp_path):
        def make(src):
            # 先頭だけ PDF で、中身は壊れている。pypdf は読めない。
            (src / "壊れた.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00\xff" * 200)

        _result, report = self._run(tmp_path, make)
        assert "走査できず" in report

    def test_images_are_not_counted_as_unscanned(self, tmp_path):
        """元から対象外のもの（画像）を「読めなかった」に数えない。"""
        def make(src):
            (src / "写真.bin").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        _result, report = self._run(tmp_path, make)
        assert "走査できず" not in report

    def test_readable_text_is_marked_scanned(self, tmp_path):
        def make(src):
            (src / "a.txt").write_text("ふつうの本文\n", encoding="utf-8")

        _result, report = self._run(tmp_path, make)
        assert "個人情報(PII)スキャン: 実施" in report
        assert "走査できず" not in report
