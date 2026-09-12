"""できあがったパッケージを、人が読める形にまとめられること。

画面に出すのはここで作った表なので、**中身が空になっていないか**を押さえる。
「ファイルは並ぶが、処理の記録が 1 件も出ない」といった壊れ方は、
画面を見ないと気づけない。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archival_packager.core import aip_pipeline, package_report, sip_pipeline
from archival_packager.core.aip_models import AIPOptions
from archival_packager.core.models import SIPMetadata, SIPOptions


@pytest.fixture
def sip(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    (src / "文書").mkdir(parents=True)
    (src / "a.txt").write_text("資料 A\n", encoding="utf-8")
    (src / "文書" / "b.txt").write_text("資料 B\n", encoding="utf-8")

    out = tmp_path / "sip-out"
    out.mkdir()
    result = sip_pipeline.run(
        input_path=src,
        output_parent=out,
        metadata=SIPMetadata(identifier="2026-移管", title="総務課文書"),
        options=SIPOptions(),
        progress=lambda _m: None,
    )
    return result.sip_path


@pytest.fixture
def aip(sip: Path, tmp_path: Path) -> Path:
    out = tmp_path / "aip-out"
    out.mkdir()
    result = aip_pipeline.run(
        sip_root=sip,
        output_parent=out,
        options=AIPOptions(),
        progress=lambda _m: None,
    )
    return result.aip_path


class TestAIP:
    def test_reads_the_overview(self, aip: Path):
        report = package_report.read(aip)
        assert report.overview.kind == "AIP"
        assert report.overview.title == "総務課文書"
        assert report.overview.identifier == "2026-移管"
        assert report.overview.created, "作成日時が空"
        assert report.overview.file_count > 0
        assert report.overview.total_bytes > 0

    def test_lists_files_with_their_technical_facts(self, aip: Path):
        report = package_report.read(aip)
        originals = [f for f in report.files if f.use == "原本"]
        assert originals, "原本が 1 件も出ていない"
        for f in originals:
            assert f.path
            assert f.sha256, f"{f.path} のチェックサムが空"
            assert f.size > 0

    def test_shows_what_was_done(self, aip: Path):
        """**ここが本題。** PREMIS のイベントが読めなければ、
        「処理の実行がそのまま保存処理記録になる」と言えない。"""
        report = package_report.read(aip)
        assert report.events, "処理の記録が 1 件も出ていない"
        labels = {e.type_label for e in report.events}
        assert "取り込み" in labels
        assert "フォーマットの識別" in labels
        for e in report.events:
            assert e.date_time, f"{e.type_label} に日時が無い"
            assert e.outcome, f"{e.type_label} に結果が無い"

    def test_events_name_the_tool(self, aip: Path):
        """どのツールで行ったかが分からないと、記録として使えない。"""
        report = package_report.read(aip)
        identified = [e for e in report.events if e.type_label == "フォーマットの識別"]
        assert identified
        assert any(e.agent for e in identified), "実行したツールが空"

    def test_events_are_in_order(self, aip: Path):
        report = package_report.read(aip)
        stamps = [e.date_time for e in report.events]
        assert stamps == sorted(stamps)


class TestSIP:
    def test_reads_the_inventory(self, sip: Path):
        report = package_report.read(sip)
        assert report.overview.kind == "SIP"
        assert report.overview.file_count > 0
        assert report.overview.title == "総務課文書"
        assert all(f.sha256 for f in report.files)

    def test_keeps_the_virus_column(self, sip: Path):
        """検査したかどうかは、表から読めること。"""
        report = package_report.read(sip)
        assert all(f.virus for f in report.files)


class TestBrokenInput:
    def test_empty_directory_does_not_raise(self, tmp_path: Path):
        report = package_report.read(tmp_path)
        assert report.overview.note, "読めなかった理由を伝えていない"
        assert report.files == []

    def test_broken_mets_is_reported(self, tmp_path: Path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "METS.broken.xml").write_text("<mets", encoding="utf-8")
        report = package_report.read(tmp_path)
        assert "METS を読めませんでした" in report.overview.note


def test_human_bytes():
    assert package_report.human_bytes(512) == "512 B"
    assert package_report.human_bytes(2048) == "2.0 KB"


class TestSubmissionDocuments:
    """提出書類は PREMIS の object を持たない。それでも一覧に出し、
    サイズは実物から測ること（0 B と並ぶと壊れて見える）。"""

    def test_listed_with_their_size(self, aip: Path):
        report = package_report.read(aip)
        docs = [f for f in report.files if f.use == "提出書類"]
        assert docs, "提出書類が一覧に出ていない"
        assert all(f.size > 0 for f in docs), "サイズが 0 のままの行がある"

    def test_counted_apart_from_originals(self, aip: Path):
        report = package_report.read(aip)
        assert report.overview.original_count < report.overview.file_count
        assert report.overview.original_count > 0


class TestNormalizedCopiesAreNamed:
    """変換して作ったファイルにも、フォーマット名を付けること。

    PUID だけ入れて名前を空にしていたため、METS には規定値の "unknown" が
    書かれ、画面では「未識別」に見えていた。変換したものが未識別に見えるのは
    実態と違ううえ、担当者を無用に不安にさせる。
    """

    @pytest.fixture
    def aip_with_png(self, tmp_path: Path) -> Path:
        import base64

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        src = tmp_path / "in"
        src.mkdir()
        (src / "写真.png").write_bytes(png)

        out = tmp_path / "sip-out"
        out.mkdir()
        sip = sip_pipeline.run(
            input_path=src, output_parent=out,
            metadata=SIPMetadata(identifier="x", title="写真"),
            options=SIPOptions(), progress=lambda _m: None,
        ).sip_path

        aout = tmp_path / "aip-out"
        aout.mkdir()
        return aip_pipeline.run(
            sip_root=sip, output_parent=aout, options=AIPOptions(),
            progress=lambda _m: None,
        ).aip_path

    def test_the_original_png_is_identified(self, aip_with_png: Path):
        report = package_report.read(aip_with_png)
        png = next(f for f in report.files if f.path.endswith(".png"))
        assert png.puid == "fmt/11"
        assert png.format_name == "Portable Network Graphics"

    def test_the_preservation_copy_has_a_name(self, aip_with_png: Path):
        report = package_report.read(aip_with_png)
        tiff = next(f for f in report.files if f.use == "保存用")
        assert tiff.puid == "fmt/353"
        assert tiff.format_name and tiff.format_name != "unknown", (
            "変換後のファイルが未識別に見える"
        )

    def test_nothing_counts_as_unidentified(self, aip_with_png: Path):
        report = package_report.read(aip_with_png)
        assert report.summary.unidentified == 0
        assert report.summary.normalized == 1


class TestSummary:
    """まとまりで見た数。1 件ずつ並べても全体は掴めない。"""

    def test_counts_formats_and_events(self, aip: Path):
        report = package_report.read(aip)
        assert report.summary.formats, "フォーマットごとの件数が空"
        assert sum(n for _name, n in report.summary.formats) == report.overview.original_count
        assert report.summary.events, "処理の種類ごとの件数が空"

    def test_formats_are_ordered_by_count(self, aip: Path):
        report = package_report.read(aip)
        counts = [n for _name, n in report.summary.formats]
        assert counts == sorted(counts, reverse=True)

    def test_inherits_the_virus_column_into_the_aip(self, aip: Path):
        """ウイルス検査の結果は METS では表せないので、
        引き継いだ技術インベントリから拾えていること。"""
        report = package_report.read(aip)
        originals = [f for f in report.files if f.use == "原本"]
        assert all(f.virus for f in originals), "ウイルス検査の列が空"
