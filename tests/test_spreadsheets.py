"""CSV 生成と受入記録の突合のテスト。"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

import pytest

from archival_packager.core import accession, spreadsheets
from archival_packager.core.models import PIIFinding, ScannedFile, SIPMetadata


def sf(rel: str, **kw) -> ScannedFile:
    return ScannedFile(
        relative_path=rel,
        absolute_path=Path("/x") / rel,
        size_bytes=kw.pop("size_bytes", 100),
        modified=kw.pop("modified", datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)),
        sha256=kw.pop("sha256", "a" * 64),
        **kw,
    )


def parse_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


class TestLineEndingsAndEscaping:
    def test_crlf_line_endings(self):
        """Excel 互換のため CRLF。"""
        out = spreadsheets.formats([sf("a.txt")])
        assert out.endswith("\r\n")
        assert "\r\n" in out

    @pytest.mark.parametrize(
        "hostile",
        ["comma,inside.txt", 'quote"inside.txt', "newline\ninside.txt", "both,\"x\".txt"],
    )
    def test_hostile_filenames_round_trip(self, hostile):
        """CSV の引用規則は標準ライブラリに任せている。往復して壊れないこと。"""
        out = spreadsheets.formats([sf(hostile)])
        rows = parse_csv(out)
        assert rows[1][0] == hostile

    def test_plain_fields_are_not_quoted(self):
        """不要な引用を付けると人が読みにくい（QUOTE_MINIMAL であること）。"""
        out = spreadsheets.formats([sf("plain.txt")])
        assert '"plain.txt"' not in out
        assert "plain.txt" in out


class TestDescription:
    def test_atom_headers_are_fixed(self):
        """AtoM のインポートが認識する固定文字列。変えてはいけない。"""
        rows = parse_csv(spreadsheets.description([sf("a.txt")], SIPMetadata("id", "t")))
        assert rows[0] == list(spreadsheets.ATOM_HEADERS)
        assert len(rows[0]) == 26

    def test_single_row_for_the_whole_sip(self):
        rows = parse_csv(
            spreadsheets.description([sf("a.txt"), sf("b.txt")], SIPMetadata("id", "t"))
        )
        assert len(rows) == 2, "見出し + 1 行"

    def test_values_land_in_the_expected_columns(self):
        meta = SIPMetadata(identifier="2026-移管", title="総務課文書", date_note="2024–2025")
        rows = parse_csv(spreadsheets.description([sf("a.txt")], meta))
        header, values = rows[0], rows[1]
        assert values[header.index("Identifier")] == "2026-移管"
        assert values[header.index("Title")] == "総務課文書"
        assert values[header.index("Date expression")] == "2024–2025"
        assert values[header.index("Level of description")] == "File"

    def test_identifier_falls_back_to_title(self):
        rows = parse_csv(spreadsheets.description([sf("a.txt")], SIPMetadata("", "タイトルのみ")))
        assert rows[1][rows[0].index("Identifier")] == "タイトルのみ"

    def test_date_range_from_file_mtimes(self):
        files = [
            sf("old.txt", modified=datetime(2024, 1, 2, tzinfo=timezone.utc)),
            sf("new.txt", modified=datetime(2025, 12, 31, tzinfo=timezone.utc)),
        ]
        rows = parse_csv(spreadsheets.description(files, SIPMetadata("id", "t")))
        header, values = rows[0], rows[1]
        assert values[header.index("Date start")] == "2024-01-02"
        assert values[header.index("Date end")] == "2025-12-31"

    def test_extent_counts_files_and_bytes(self):
        files = [sf("a.txt", size_bytes=1500), sf("b.txt", size_bytes=2500)]
        rows = parse_csv(spreadsheets.description(files, SIPMetadata("id", "t")))
        extent = rows[1][rows[0].index("Extent and medium")]
        assert extent.startswith("2 digital files (")
        assert "4.0 kB" in extent

    def test_scope_falls_back_to_format_summary(self):
        files = [sf("a.pdf", format_name="PDF"), sf("b.pdf", format_name="PDF"),
                 sf("c.txt", format_name="Plain Text")]
        rows = parse_csv(spreadsheets.description(files, SIPMetadata("id", "t")))
        scope = rows[1][rows[0].index("Scope and content")]
        assert scope.startswith("Most common file formats:")
        assert scope.index("PDF") < scope.index("Plain Text"), "件数の多い順"

    def test_format_summary_is_deterministic_for_ties(self):
        """同数のときの順序が走査順に依存すると、同じ資料から違う CSV が出る。"""
        a = [sf("1.x", format_name="Bravo"), sf("2.x", format_name="Alpha")]
        b = [sf("1.x", format_name="Alpha"), sf("2.x", format_name="Bravo")]
        scope_a = parse_csv(spreadsheets.description(a, SIPMetadata("i", "t")))[1][9]
        scope_b = parse_csv(spreadsheets.description(b, SIPMetadata("i", "t")))[1][9]
        assert scope_a == scope_b

    def test_explicit_scope_wins(self):
        meta = SIPMetadata("id", "t", scope_note="手で書いた説明")
        rows = parse_csv(spreadsheets.description([sf("a.pdf")], meta))
        assert rows[1][rows[0].index("Scope and content")] == "手で書いた説明"


class TestFormats:
    def test_columns(self):
        rows = parse_csv(spreadsheets.formats([sf("a.pdf", puid="fmt/19", mime_type="application/pdf",
                                                  format_name="PDF 1.5", format_warning="")]))
        assert rows[0][:4] == ["相対パス", "フォーマット名", "PRONOM", "MIME"]
        assert rows[1][:4] == ["a.pdf", "PDF 1.5", "fmt/19", "application/pdf"]

    def test_missing_values_become_empty_not_none(self):
        rows = parse_csv(spreadsheets.formats([sf("a.bin", sha256=None)]))
        assert "None" not in "".join(rows[1])


class TestAccessionCSV:
    def test_records_original_path_when_sanitized(self):
        f = sf("a_b.txt", original_relative_path="a:b.txt")
        rows = parse_csv(spreadsheets.accession([f]))
        assert rows[1][0] == "a:b.txt", "受入時の元パスを残す"

    def test_records_current_path_when_not_sanitized(self):
        rows = parse_csv(spreadsheets.accession([sf("plain.txt")]))
        assert rows[1][0] == "plain.txt"

    def test_iso8601_utc(self):
        f = sf("a.txt", modified=datetime(2026, 7, 25, 3, 4, 5, tzinfo=timezone.utc))
        rows = parse_csv(spreadsheets.accession([f]))
        assert rows[1][3] == "2026-07-25T03:04:05Z"


class TestPIIReport:
    def test_none_when_no_findings(self):
        """空ファイルを作ると「検出あり」と誤解される。"""
        assert spreadsheets.pii_report([sf("a.txt")]) is None

    def test_rows_for_each_finding(self):
        f = sf("a.txt", pii=[
            PIIFinding(kind="email", masked="a***@example.com", line=3),
            PIIFinding(kind="phone", masked="090-****-1234", line=7),
        ])
        rows = parse_csv(spreadsheets.pii_report([f]))
        assert rows[0] == ["相対パス", "種別", "検出値（マスク）", "行"]
        assert len(rows) == 3
        assert rows[1] == ["a.txt", "email", "a***@example.com", "3"]

    def test_line_may_be_unknown(self):
        f = sf("a.pdf", pii=[PIIFinding(kind="email", masked="a***@b", line=None)])
        rows = parse_csv(spreadsheets.pii_report([f]))
        assert rows[1][3] == ""


class TestMetadataTemplate:
    def test_has_whole_sip_row_first(self):
        rows = parse_csv(spreadsheets.metadata_template([sf("a.txt")]))
        assert rows[0][0] == "filename"
        assert rows[1][0] == "objects/", "SIP 全体行"
        assert rows[2][0] == "objects/a.txt"

    def test_dc_columns_are_blank_for_filling_in(self):
        rows = parse_csv(spreadsheets.metadata_template([sf("a.txt")]))
        assert all(v == "" for v in rows[2][1:])


class TestAccessionParsing:
    def test_reads_by_header_name_not_position(self):
        """列順が変わっても読めること（列見出しで引く）。"""
        text = "SHA-256,原パス（受入時）,サイズ(バイト)\r\nabc,a.txt,10\r\n"
        recs = accession.parse(text)
        assert recs == [accession.AccessionRecord(original_path="a.txt", sha256="abc")]

    def test_strips_bom(self):
        text = "﻿原パス（受入時）,SHA-256\r\na.txt,abc\r\n"
        assert accession.parse(text)[0].sha256 == "abc"

    def test_skips_rows_without_hash(self):
        text = "原パス（受入時）,SHA-256\r\na.txt,\r\nb.txt,abc\r\n"
        recs = accession.parse(text)
        assert [r.original_path for r in recs] == ["b.txt"]

    def test_handles_quoted_fields(self):
        text = '原パス（受入時）,SHA-256\r\n"a,b.txt",abc\r\n'
        assert accession.parse(text)[0].original_path == "a,b.txt"

    def test_handles_newline_inside_quoted_field(self):
        """Swift 版は先に行で切るため、値に改行を含む CSV を正しく読めなかった。"""
        text = '原パス（受入時）,SHA-256\r\n"line1\nline2.txt",abc\r\n'
        recs = accession.parse(text)
        assert recs[0].original_path == "line1\nline2.txt"

    def test_handles_doubled_quotes(self):
        text = '原パス（受入時）,SHA-256\r\n"say ""hi"".txt",abc\r\n'
        assert accession.parse(text)[0].original_path == 'say "hi".txt'

    @pytest.mark.parametrize("text", ["", "   ", "﻿"])
    def test_empty_input(self, text):
        assert accession.parse(text) == []

    def test_missing_columns_yield_empty_strings(self):
        text = "何か別の列\r\n値\r\n"
        assert accession.parse(text) == [], "SHA-256 列が無ければ突合できないので落とす"


class TestArrangementJoin:
    def test_matches_by_content_hash_across_rearrangement(self):
        """配列（フォルダ並べ替え）後もハッシュで追跡できること。"""
        prior = [accession.AccessionRecord("受入/a.txt", "h1")]
        current = [sf("整理後/2024/a.txt", sha256="h1")]
        rows = accession.join(current, prior)
        assert len(rows) == 1
        assert rows[0].matched
        assert rows[0].before == "受入/a.txt"
        assert rows[0].after == "整理後/2024/a.txt"
        assert rows[0].key_kind == "SHA-256"

    def test_new_file_is_flagged(self):
        rows = accession.join([sf("new.txt", sha256="h9")], [])
        assert not rows[0].matched
        assert rows[0].before == ""

    def test_missing_file_is_flagged(self):
        rows = accession.join([], [accession.AccessionRecord("gone.txt", "h1")])
        assert not rows[0].matched
        assert rows[0].after == ""

    def test_duplicate_content_consumes_one_prior_each(self):
        """内容が同一のファイルが複数あるとき、prior を使い回さないこと。"""
        prior = [accession.AccessionRecord("a1.txt", "same"),
                 accession.AccessionRecord("a2.txt", "same")]
        current = [sf("x.txt", sha256="same"), sf("y.txt", sha256="same")]
        rows = accession.join(current, prior)
        matched = [r for r in rows if r.matched]
        assert len(matched) == 2
        assert {r.before for r in matched} == {"a1.txt", "a2.txt"}

    def test_more_current_than_prior_leaves_extras_unmatched(self):
        prior = [accession.AccessionRecord("a1.txt", "same")]
        current = [sf("x.txt", sha256="same"), sf("y.txt", sha256="same")]
        rows = accession.join(current, prior)
        assert sum(1 for r in rows if r.matched) == 1
        assert sum(1 for r in rows if not r.matched) == 1

    def test_csv_verdicts(self):
        rows = [
            accession.ArrangementRow("a", "b", "SHA-256", "h", True),
            accession.ArrangementRow("", "new.txt", "未突合", "", False),
            accession.ArrangementRow("gone.txt", "", "未突合", "h", False),
        ]
        parsed = parse_csv(accession.to_csv(rows))
        assert parsed[0] == ["配列前パス", "配列後パス", "突合キー種別", "キー", "判定"]
        assert parsed[1][4] == "一致"
        assert "新規" in parsed[2][4]
        assert "欠落" in parsed[3][4]
