"""SIP 組み立てのテスト。

レイアウト（本家 sipcreator 準拠）と、BagIt 仕様適合を固定する。
bag は自前実装ではなく bagit（米国議会図書館のリファレンス実装）に委ねているので、
「自分で書いたマニフェストが正しいか」ではなく「bagit の検証を通るか」を見る。
"""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest

from archival_packager.core import sip_builder
from archival_packager.core.checksums import sha256_of
from archival_packager.core.models import (
    PIIFinding,
    ScannedFile,
    SIPMetadata,
    SIPOptions,
    SIPPipelineError,
)
from archival_packager.core.sip_builder import SIPBuildRequest, SubmissionDocs


def docs() -> SubmissionDocs:
    return SubmissionDocs(
        description_csv="col\nvalue\n",
        formats_csv="puid,count\n",
        accession_csv="path,sha256\n",
        metadata_csv="filename\n",
        dfxml_xml="<dfxml/>\n",
        report_text="report\n",
        report_html="<html></html>\n",
    )


@pytest.fixture
def payload(tmp_path: Path) -> tuple[Path, list[ScannedFile]]:
    """入力ツリーを作り、SHA-256 まで埋めた ScannedFile を返す。"""
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_bytes(b"alpha")
    (src / "sub" / "b.txt").write_bytes(b"bravo")

    files = []
    for rel in ("a.txt", "sub/b.txt"):
        abs_path = src / rel
        files.append(
            ScannedFile(
                relative_path=rel,
                absolute_path=abs_path,
                size_bytes=abs_path.stat().st_size,
                modified=datetime.fromtimestamp(abs_path.stat().st_mtime, UTC),
                sha256=sha256_of(abs_path),
                puid="fmt/111",
            )
        )
    return src, files


def request_for(payload, tmp_path: Path, **opts) -> SIPBuildRequest:
    src, files = payload
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return SIPBuildRequest(
        input_root=src,
        output_parent=out,
        files=files,
        metadata=SIPMetadata(identifier="2026-移管-総務課", title="テスト移管"),
        options=SIPOptions(**opts),
    )


class TestPlainLayout:
    def test_layout_matches_sipcreator_convention(self, payload, tmp_path):
        result = sip_builder.build(request_for(payload, tmp_path), docs())
        pkg = result.sip_path

        assert (pkg / "objects" / "a.txt").read_bytes() == b"alpha"
        assert (pkg / "objects" / "sub" / "b.txt").read_bytes() == b"bravo", "階層を保つ"

        subdoc = pkg / "metadata" / "submissionDocumentation"
        for name in ("description.csv", "formats.csv", "accession.csv", "dfxml.xml",
                     "report.txt", "report.html", "checksum.sha256"):
            assert (subdoc / name).is_file(), name

        # metadata.csv だけは Archivematica 規約で metadata/ 直下。
        assert (pkg / "metadata" / "metadata.csv").is_file()
        assert not (subdoc / "metadata.csv").exists()

    def test_checksum_manifest_format(self, payload, tmp_path):
        """"<hash>␣␣objects/<rel>" 形式（スペース 2 個）。"""
        result = sip_builder.build(request_for(payload, tmp_path), docs())
        manifest = (
            result.sip_path / "metadata" / "submissionDocumentation" / "checksum.sha256"
        ).read_text(encoding="utf-8")

        _src, files = payload
        for f in files:
            assert f"{f.sha256}  objects/{f.relative_path}" in manifest
        assert manifest.endswith("\n")

    def test_csv_has_utf8_bom(self, payload, tmp_path):
        """Excel が UTF-8 と判定するために BOM が要る。日本語が化けると現場で困る。"""
        result = sip_builder.build(request_for(payload, tmp_path), docs())
        raw = (
            result.sip_path / "metadata" / "submissionDocumentation" / "description.csv"
        ).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")

    def test_non_csv_has_no_bom(self, payload, tmp_path):
        """XML に BOM を付けるとパーサによっては読めなくなる。"""
        result = sip_builder.build(request_for(payload, tmp_path), docs())
        raw = (result.sip_path / "metadata" / "submissionDocumentation" / "dfxml.xml").read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")

    def test_optional_docs_absent_when_empty(self, payload, tmp_path):
        """該当が無いときは空ファイルを作らない（あると「検出あり」と誤解される）。"""
        result = sip_builder.build(request_for(payload, tmp_path), docs())
        subdoc = result.sip_path / "metadata" / "submissionDocumentation"
        assert not (subdoc / "arrangement-map.csv").exists()
        assert not (subdoc / "pii-report.csv").exists()
        assert result.pii_report_path is None

    def test_optional_docs_written_when_present(self, payload, tmp_path):
        d = docs()
        d.pii_csv = "path,kind\na.txt,email\n"
        result = sip_builder.build(request_for(payload, tmp_path), d)
        assert result.pii_report_path is not None
        assert result.pii_report_path.is_file()


class TestPackageNaming:
    def test_uses_identifier(self, payload, tmp_path):
        result = sip_builder.build(request_for(payload, tmp_path), docs())
        assert result.sip_path.name == "2026-移管-総務課", "日本語をそのまま使う"

    def test_collision_gets_suffix(self, payload, tmp_path):
        first = sip_builder.build(request_for(payload, tmp_path), docs())
        second = sip_builder.build(request_for(payload, tmp_path), docs())
        assert first.sip_path != second.sip_path, "既存を上書きしない"
        assert second.sip_path.name == "2026-移管-総務課 2"

    def test_path_separators_are_neutralised(self, payload, tmp_path):
        src, files = payload
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        req = SIPBuildRequest(
            input_root=src,
            output_parent=out,
            files=files,
            metadata=SIPMetadata(identifier="a/b:c", title="t"),
            options=SIPOptions(),
        )
        result = sip_builder.build(req, docs())
        assert result.sip_path.name == "a_b_c"
        assert result.sip_path.parent == out, "出力先の外に出ない"

    def test_falls_back_to_title_then_default(self, payload, tmp_path):
        src, files = payload
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        req = SIPBuildRequest(
            input_root=src, output_parent=out, files=files,
            metadata=SIPMetadata(identifier="  ", title="  "), options=SIPOptions(),
        )
        assert sip_builder.build(req, docs()).sip_path.name == "SIP"


class TestBagLayout:
    def test_bag_validates_against_the_spec(self, payload, tmp_path):
        """自前実装では確認できなかった「仕様適合」を検証する。"""
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        assert result.bagged
        sip_builder.validate_bag(result.sip_path)  # 失敗すれば例外

    def test_payload_moved_under_data(self, payload, tmp_path):
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        pkg = result.sip_path
        assert (pkg / "data" / "objects" / "a.txt").read_bytes() == b"alpha"
        assert (pkg / "data" / "metadata" / "metadata.csv").is_file()
        assert not (pkg / "objects").exists(), "bag 化後は data/ 配下のみ"

    def test_tag_files_present(self, payload, tmp_path):
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        pkg = result.sip_path
        for name in ("bagit.txt", "bag-info.txt", "manifest-sha256.txt", "tagmanifest-sha256.txt"):
            assert (pkg / name).is_file(), name

    def test_sha256_is_used_not_md5(self, payload, tmp_path):
        """本家 sipcreator は MD5 だが、こちらは SHA-256 を使う（Swift 版から引き継ぐ改善）。"""
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        assert not (result.sip_path / "manifest-md5.txt").exists()
        manifest = (result.sip_path / "manifest-sha256.txt").read_text(encoding="utf-8")
        _src, files = payload
        assert files[0].sha256 in manifest

    def test_external_identifier_recorded(self, payload, tmp_path):
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        info = (result.sip_path / "bag-info.txt").read_text(encoding="utf-8")
        assert "External-Identifier: 2026-移管-総務課" in info
        assert "External-Description: テスト移管" in info

    def test_payload_oxum_is_generated(self, payload, tmp_path):
        """Payload-Oxum は bagit 側が算出する。二重に書いていないこと。"""
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        info = (result.sip_path / "bag-info.txt").read_text(encoding="utf-8")
        assert info.count("Payload-Oxum:") == 1

    def test_result_paths_follow_the_move_into_data(self, payload, tmp_path):
        """bag 化でファイルが data/ へ移るので、返すパスも追従していること。"""
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        assert result.spreadsheet_path is not None
        assert result.spreadsheet_path.is_file(), "存在しないパスを返さない"
        assert "data" in result.spreadsheet_path.parts

    def test_tampered_payload_is_detected(self, payload, tmp_path):
        """検証が実際に効いていること（通るだけのテストにしない）。"""
        result = sip_builder.build(request_for(payload, tmp_path, make_bag=True), docs())
        (result.sip_path / "data" / "objects" / "a.txt").write_bytes(b"tampered")
        with pytest.raises(SIPPipelineError):
            sip_builder.validate_bag(result.sip_path)


class TestWarnings:
    def test_collects_points_needing_review(self):
        files = [
            ScannedFile("v.txt", Path("/x/v.txt"), 1, datetime.fromtimestamp(0, UTC), virus="Eicar"),
            ScannedFile("p.txt", Path("/x/p.txt"), 1, datetime.fromtimestamp(0, UTC), puid="fmt/1",
                        pii=[PIIFinding(kind="email", masked="a***@b")]),
            ScannedFile("u.bin", Path("/x/u.bin"), 1, datetime.fromtimestamp(0, UTC)),
            ScannedFile("m.txt", Path("/x/m.txt"), 1, datetime.fromtimestamp(0, UTC), puid="fmt/2",
                        format_warning="extension mismatch"),
        ]
        w = "\n".join(sip_builder.collect_warnings(files))
        assert "ウイルス検出: v.txt (Eicar)" in w
        assert "PII候補: p.txt (1)" in w
        assert "未識別: u.bin" in w
        assert "拡張子不一致: m.txt" in w

    def test_identified_clean_file_produces_no_warning(self):
        f = ScannedFile("ok.txt", Path("/x/ok.txt"), 1, datetime.fromtimestamp(0, UTC), puid="fmt/1")
        assert sip_builder.collect_warnings([f]) == []


class TestGuards:
    def test_empty_input_raises(self, tmp_path):
        req = SIPBuildRequest(
            input_root=tmp_path, output_parent=tmp_path, files=[],
            metadata=SIPMetadata(identifier="x", title="y"), options=SIPOptions(),
        )
        with pytest.raises(SIPPipelineError) as exc:
            sip_builder.build(req, docs())
        assert "対象ファイルがありません" in exc.value.message

    def test_long_paths_are_flagged(self, payload, tmp_path):
        """macOS で作った SIP を Windows で開けない、という後で露見する問題を先に検出する。"""
        _src, files = payload
        deep = files[0]
        deep.relative_path = "/".join(["長い名前のフォルダ"] * 20) + "/file.txt"
        warnings = sip_builder.check_path_lengths(tmp_path / "pkg", [deep])
        assert warnings and "パスが長すぎます" in warnings[0]


class TestUnicodeNormalizationCheck:
    """記録名が NFC でないことを、黙って直さずに警告する。

    記録文字列だけ NFC に直すと、実ファイルが NFD のままの bag ができ、
    正規化に鈍感でない NTFS / ext4 では「マニフェストのファイルが無い」
    となる。作った macOS でだけ検証が通る状態が最も危ない。
    """

    # ソースの保存形に依存しないよう、リテラルではなく明示的に構成する。
    NFD = unicodedata.normalize("NFD", "が.txt")  # macOS が返しうる分解形
    NFC = unicodedata.normalize("NFC", "が.txt")  # 合成形

    def _file(self, rel):
        return ScannedFile(rel, Path("/x") / rel, 1, datetime.fromtimestamp(0, UTC))

    def test_decomposed_name_is_flagged(self):
        warnings = sip_builder.check_unicode_normalization(
            [self._file(self.NFD)], sanitized=False
        )
        assert any("NFC 正規化されていません" in w for w in warnings)

    def test_guidance_points_at_sanitize_when_it_is_off(self):
        warnings = sip_builder.check_unicode_normalization(
            [self._file(self.NFD)], sanitized=False
        )
        assert "ファイル名を安全化" in warnings[0]

    def test_no_guidance_line_when_sanitize_already_ran(self):
        # sanitize 済みで残っているなら、案内しても仕方がない（既に有効）。
        warnings = sip_builder.check_unicode_normalization(
            [self._file(self.NFD)], sanitized=True
        )
        assert warnings and "ファイル名を安全化" not in warnings[0]

    def test_composed_name_is_silent(self):
        assert sip_builder.check_unicode_normalization(
            [self._file(self.NFC)], sanitized=False
        ) == []

    def test_ascii_names_are_silent(self):
        assert sip_builder.check_unicode_normalization(
            [self._file("plain.txt"), self._file("a/b.pdf")], sanitized=False
        ) == []

    def test_sanitize_removes_the_condition(self):
        """sanitize は実ファイルごと NFC 名にするので、警告対象が消える。"""
        from archival_packager.core import filenames

        sanitized, _ = filenames.apply([self._file(self.NFD)], normalize_nfc=True)
        assert sanitized[0].relative_path == self.NFC
        assert sip_builder.check_unicode_normalization(sanitized, sanitized=True) == []
