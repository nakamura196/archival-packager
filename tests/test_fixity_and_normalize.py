"""完全性確認と正規化のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archival_packager.core import conversion_registry, fixity, normalizer
from archival_packager.core.aip_models import (
    AIPFile,
    AIPPipelineError,
    DerivativePurpose,
    FixityOutcome,
    NormalizationRule,
)
from archival_packager.core.checksums import sha256_of


def make_plain_sip(root: Path, files: dict[str, bytes], *, corrupt: str | None = None) -> Path:
    """非 bag の SIP を作る。corrupt を指定するとマニフェスト記載後に中身を書き換える。"""
    objects = root / "objects"
    objects.mkdir(parents=True)
    subdoc = root / "metadata" / "submissionDocumentation"
    subdoc.mkdir(parents=True)

    lines = []
    for rel, data in files.items():
        target = objects / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        lines.append(f"{sha256_of(target)}  objects/{rel}")

    (subdoc / "checksum.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if corrupt:
        (objects / corrupt).write_bytes(b"tampered")
    return root


class TestFixity:
    def test_passes_when_everything_matches(self, tmp_path):
        make_plain_sip(tmp_path, {"a.txt": b"alpha", "d/b.txt": b"bravo"})
        status = fixity.verify(tmp_path, is_bag=False)
        assert status.outcome is FixityOutcome.PASSED
        assert status.checked == 2

    def test_detects_tampering(self, tmp_path):
        make_plain_sip(tmp_path, {"a.txt": b"alpha", "b.txt": b"bravo"}, corrupt="a.txt")
        status = fixity.verify(tmp_path, is_bag=False)
        assert status.outcome is FixityOutcome.FAILED
        assert status.mismatches == ["a.txt".replace("a.txt", "objects/a.txt")]

    def test_reports_missing_file_distinctly(self, tmp_path):
        make_plain_sip(tmp_path, {"a.txt": b"alpha"})
        (tmp_path / "objects" / "a.txt").unlink()
        status = fixity.verify(tmp_path, is_bag=False)
        assert status.outcome is FixityOutcome.FAILED
        assert "ファイル無し" in status.mismatches[0]

    def test_skips_when_manifest_absent(self, tmp_path):
        (tmp_path / "objects").mkdir()
        status = fixity.verify(tmp_path, is_bag=False)
        assert status.outcome is FixityOutcome.SKIPPED
        assert "見つかりません" in status.reason

    def test_skips_when_manifest_has_no_usable_lines(self, tmp_path):
        subdoc = tmp_path / "metadata" / "submissionDocumentation"
        subdoc.mkdir(parents=True)
        (subdoc / "checksum.sha256").write_text("\n\n   \n", encoding="utf-8")
        status = fixity.verify(tmp_path, is_bag=False)
        assert status.outcome is FixityOutcome.SKIPPED

    def test_bag_manifest_location(self, tmp_path):
        data = tmp_path / "data" / "objects"
        data.mkdir(parents=True)
        (data / "a.txt").write_bytes(b"alpha")
        digest = sha256_of(data / "a.txt")
        (tmp_path / "manifest-sha256.txt").write_text(
            f"{digest}  data/objects/a.txt\n", encoding="utf-8"
        )
        status = fixity.verify(tmp_path, is_bag=True)
        assert status.outcome is FixityOutcome.PASSED


class TestManifestLineParsing:
    @pytest.mark.parametrize(
        "line,expected",
        [
            ("abc  objects/a.txt", ("abc", "objects/a.txt")),
            ("abc\tobjects/a.txt", ("abc", "objects/a.txt")),
            ("abc objects/a.txt", ("abc", "objects/a.txt")),
            ("abc  *objects/a.txt", ("abc", "objects/a.txt")),  # md5deep のバイナリ印
            ("  abc  objects/a.txt  ", ("abc", "objects/a.txt")),
        ],
    )
    def test_accepted_forms(self, line, expected):
        assert fixity._parse_line(line) == expected

    @pytest.mark.parametrize("line", ["", "   ", "hashonly"])
    def test_rejected_forms(self, line):
        assert fixity._parse_line(line) is None

    def test_path_with_spaces_is_kept_whole(self):
        """資料名に空白が入るのは普通のこと。最初の区切りだけで切る。"""
        assert fixity._parse_line("abc  objects/報告書 最終版.pdf") == (
            "abc",
            "objects/報告書 最終版.pdf",
        )


class TestConversionRegistry:
    def test_images_go_to_tiff_via_imagemagick(self):
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        assert rule is not None
        assert rule.tool == "magick", "sips は Windows に無いので使わない"
        assert rule.out_extension == "tiff"
        assert rule.puid_out == conversion_registry.TIFF_PUID

    def test_image_conversion_is_lossless(self):
        """保存用途で非可逆圧縮が既定で掛かると取り返しがつかない。"""
        rule = conversion_registry.rule_for("fmt/41", DerivativePurpose.PRESERVATION)
        assert "-compress" in rule.args and "none" in rule.args

    def test_postscript_goes_to_pdf_via_ghostscript(self):
        rule = conversion_registry.rule_for("fmt/124", DerivativePurpose.PRESERVATION)
        assert rule is not None
        assert rule.tool == "gs"
        assert rule.puid_out == conversion_registry.PDF_PUID

    def test_already_preservation_ready_formats_are_left_alone(self):
        """TIFF や PDF を再変換すると劣化と情報損失を招く。"""
        assert conversion_registry.rule_for(conversion_registry.TIFF_PUID, DerivativePurpose.PRESERVATION) is None
        assert conversion_registry.rule_for(conversion_registry.PDF_PUID, DerivativePurpose.PRESERVATION) is None

    def test_unknown_puid_is_left_alone(self):
        assert conversion_registry.rule_for("fmt/99999", DerivativePurpose.PRESERVATION) is None
        assert conversion_registry.rule_for(None, DerivativePurpose.PRESERVATION) is None

    def test_access_purpose_not_supported_yet(self):
        assert conversion_registry.rule_for("fmt/11", DerivativePurpose.ACCESS) is None


class TestDerivativeNaming:
    @pytest.mark.parametrize(
        "original,ext,expected",
        [
            ("a.png", "tiff", "a-preservation.tiff"),
            ("d/sub/a.png", "tiff", "d/sub/a-preservation.tiff"),
            ("noext", "pdf", "noext-preservation.pdf"),
            ("a.tar.gz", "pdf", "a.tar-preservation.pdf"),
            ("日本語 資料.png", "tiff", "日本語 資料-preservation.tiff"),
        ],
    )
    def test_placed_next_to_the_original(self, original, ext, expected):
        assert normalizer.derivative_relative_path(original, ext) == expected

    def test_never_collides_with_the_original(self):
        """原本を上書きしないことが要件。"""
        assert normalizer.derivative_relative_path("a.tiff", "tiff") != "a.tiff"


class TestNormalizerFailures:
    def test_missing_tool_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(normalizer, "locate", lambda tool: None)
        f = AIPFile("a.png", tmp_path / "a.png", 1, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        with pytest.raises(AIPPipelineError) as exc:
            normalizer.normalize(f, rule, tmp_path / "work")
        assert "変換ツールが見つかりません" in exc.value.message

    def test_tool_that_produces_nothing_is_an_error(self, tmp_path):
        """終了コード 0 でも出力が無いことがある。

        存在しない派生物を PREMIS に記録してしまわないよう、ここで止める。
        """
        fake = tmp_path / "faketool"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)

        rule = NormalizationRule(
            puid_in="fmt/11", purpose=DerivativePurpose.PRESERVATION,
            tool=str(fake), args=["{in}", "{out}"], out_extension="tiff",
        )
        src = tmp_path / "a.png"
        src.write_bytes(b"x")
        f = AIPFile("a.png", src, 1, "u1")

        with pytest.raises(AIPPipelineError) as exc:
            normalizer.normalize(f, rule, tmp_path / "work")
        assert "出力が生成されませんでした" in exc.value.message

    def test_failing_tool_surfaces_stderr(self, tmp_path):
        fake = tmp_path / "faketool"
        fake.write_text("#!/bin/sh\necho 'boom' >&2\nexit 3\n")
        fake.chmod(0o755)

        rule = NormalizationRule(
            puid_in="fmt/11", purpose=DerivativePurpose.PRESERVATION,
            tool=str(fake), args=["{in}", "{out}"], out_extension="tiff",
        )
        src = tmp_path / "a.png"
        src.write_bytes(b"x")
        f = AIPFile("a.png", src, 1, "u1")

        with pytest.raises(AIPPipelineError) as exc:
            normalizer.normalize(f, rule, tmp_path / "work")
        assert "code 3" in exc.value.message
        assert "boom" in exc.value.message

    def test_original_is_never_modified(self, tmp_path):
        fake = tmp_path / "faketool"
        # 出力を作るが、入力にも書き込もうとする不作法なツールを模す。
        fake.write_text('#!/bin/sh\nprintf out > "$2"\n')
        fake.chmod(0o755)

        rule = NormalizationRule(
            puid_in="fmt/11", purpose=DerivativePurpose.PRESERVATION,
            tool=str(fake), args=["{in}", "{out}"], out_extension="tiff",
        )
        src = tmp_path / "a.png"
        src.write_bytes(b"original-bytes")
        f = AIPFile("a.png", src, len(b"original-bytes"), "u1")

        derivative = normalizer.normalize(f, rule, tmp_path / "work")
        assert src.read_bytes() == b"original-bytes", "原本は読むだけ"
        assert derivative.sha256 is not None
        assert derivative.tool_name == str(fake)
        assert "{in}" not in derivative.command_line, "コマンドは実パスに展開して記録する"
