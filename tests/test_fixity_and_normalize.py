"""完全性確認と正規化のテスト。"""

from __future__ import annotations

from pathlib import Path

import pytest

from archival_packager.core import conversion_registry, fixity, image_normalize, normalizer
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
    def test_images_go_to_tiff_in_process(self):
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        assert rule is not None
        assert rule.tool == image_normalize.TOOL, "sips は Windows に無いので使わない"
        assert rule.out_extension == "tiff"
        assert rule.puid_out == conversion_registry.TIFF_PUID

    def test_image_conversion_needs_no_external_binary(self):
        """同梱バイナリの有無に左右されないこと。

        外部ツールに任せると、配布先に無ければ「変換されないまま原本だけ保存」に
        静かに落ちる。画像は最も件数が多いので、そこは落とさない。
        """
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        assert rule.args == [], "外部プロセスの引数を持たない"

    def test_postscript_goes_to_pdf_via_ghostscript(self):
        rule = conversion_registry.rule_for("fmt/124", DerivativePurpose.PRESERVATION)
        assert rule is not None
        assert rule.tool == "gs"
        assert rule.puid_out == conversion_registry.PDF_PUID

    def test_already_preservation_ready_formats_are_left_alone(self):
        """TIFF や PDF を再変換すると劣化と情報損失を招く。"""
        for puid in (conversion_registry.TIFF_PUID, conversion_registry.PDF_PUID):
            assert conversion_registry.rule_for(puid, DerivativePurpose.PRESERVATION) is None

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
        """外部プロセスに頼るルール（gs）は、ツールが無ければ失敗を返す。"""
        monkeypatch.setattr(normalizer, "locate", lambda tool: None)
        f = AIPFile("a.ps", tmp_path / "a.ps", 1, "u1")
        rule = conversion_registry.rule_for("fmt/124", DerivativePurpose.PRESERVATION)
        assert rule.tool == "gs"
        with pytest.raises(AIPPipelineError) as exc:
            normalizer.normalize(f, rule, tmp_path / "work")
        assert "変換ツールが見つかりません" in exc.value.message

    def test_tool_that_produces_nothing_is_an_error(self, tmp_path, fake_tool):
        """終了コード 0 でも出力が無いことがある。

        存在しない派生物を PREMIS に記録してしまわないよう、ここで止める。
        """
        fake = fake_tool(exit_code=0)

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

    def test_failing_tool_surfaces_stderr(self, tmp_path, fake_tool):
        fake = fake_tool(exit_code=3, stderr="boom")

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

    def test_original_is_never_modified(self, tmp_path, fake_tool):
        # 出力だけを作るツールを模す。原本に触れていないことを確かめる。
        fake = fake_tool(output_text="out")

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


class TestImageNormalization:
    """画像 → 非圧縮 TIFF。外部プロセスを使わないので配布先でも必ず動く。

    ここで確かめたいのは「変換できた」ことより「変換で何も失っていない」こと。
    保存用派生物が原本より情報の少ないものになっていたら、変換しない方がまだ良い。
    """

    def _png(self, path: Path, **kw) -> Path:
        from PIL import Image

        Image.new(kw.pop("mode", "RGB"), (8, 8), kw.pop("color", (10, 20, 30))).save(path)
        return path

    def test_produces_uncompressed_tiff(self, tmp_path):
        from PIL import Image

        out = tmp_path / "out.tiff"
        image_normalize.to_tiff(self._png(tmp_path / "a.png"), out)
        with Image.open(out) as got:
            assert got.format == "TIFF"
            assert got.info.get("compression") == "raw", "非可逆どころか圧縮自体を掛けない"

    def test_same_input_gives_same_bytes(self, tmp_path):
        """OS をまたいでも同じ資料から同じ派生物が出ることが前提。

        少なくとも同一環境で決定的であることは固定しておく（非決定なら
        そもそも OS 間の一致を論じられない）。
        """
        src = self._png(tmp_path / "a.png")
        first, second = tmp_path / "1.tiff", tmp_path / "2.tiff"
        image_normalize.to_tiff(src, first)
        image_normalize.to_tiff(src, second)
        assert sha256_of(first) == sha256_of(second)

    def test_multi_frame_images_keep_every_frame(self, tmp_path):
        """アニメーション GIF を素直に保存すると 1 フレーム目以外が警告も無く消える。

        「変換したのに中身が減っている」のは最も気づきにくい壊れ方なので、
        多ページ TIFF として全フレームを残す。
        """
        from PIL import Image

        frames = [Image.new("RGB", (4, 4), c).convert("P")
                  for c in [(255, 0, 0), (0, 255, 0), (0, 0, 255)]]
        src = tmp_path / "anim.gif"
        frames[0].save(src, save_all=True, append_images=frames[1:])

        out = tmp_path / "anim.tiff"
        detail = image_normalize.to_tiff(src, out)
        with Image.open(out) as got:
            assert got.n_frames == 3
        assert "3 フレーム" in detail, "落とさなかったことを PREMIS に残す"

    def test_palette_transparency_is_preserved(self, tmp_path):
        """TIFF のパレットはアルファを持てない。P のまま書くと透過だけ消える。"""
        from PIL import Image

        im = Image.new("P", (4, 4))
        im.putpalette([255, 0, 0] * 256)
        im.info["transparency"] = 0
        src = tmp_path / "t.png"
        im.save(src)

        out = tmp_path / "t.tiff"
        image_normalize.to_tiff(src, out)
        with Image.open(out) as got:
            assert got.mode == "RGBA"
            assert got.getpixel((0, 0))[3] == 0, "透過が残っている"

    def test_bit_depth_is_not_reduced(self, tmp_path):
        """16bit を 8bit に落とすのは情報の破棄。勝手に RGB へ揃えない。"""
        from PIL import Image

        src = tmp_path / "g16.png"
        Image.new("I;16", (4, 4)).save(src)
        out = tmp_path / "g16.tiff"
        image_normalize.to_tiff(src, out)
        with Image.open(out) as got:
            assert got.mode == "I;16"

    def test_pixels_survive_the_round_trip(self, tmp_path):
        from PIL import Image

        src = tmp_path / "a.png"
        Image.new("RGB", (4, 4), (12, 34, 56)).save(src)
        out = tmp_path / "a.tiff"
        image_normalize.to_tiff(src, out)
        with Image.open(out) as got:
            assert got.size == (4, 4)
            assert got.convert("RGB").tobytes() == bytes([12, 34, 56]) * 16

    def test_detail_note_records_the_library_version(self, tmp_path):
        """出力バイト列は Pillow / libtiff の版に依存する。後から追えるようにする。"""
        detail = image_normalize.to_tiff(self._png(tmp_path / "a.png"), tmp_path / "a.tiff")
        assert "Pillow" in detail and "libtiff" in detail

    def test_broken_image_is_an_error_not_a_crash(self, tmp_path):
        """壊れたファイルで移管全体を止めない（呼び出し側が警告にする）。"""
        src = tmp_path / "broken.png"
        src.write_bytes(b"not really a png")
        f = AIPFile("broken.png", src, src.stat().st_size, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        with pytest.raises(AIPPipelineError):
            normalizer.normalize(f, rule, tmp_path / "work")

    def test_normalize_records_pillow_as_the_tool(self, tmp_path):
        src = self._png(tmp_path / "a.png")
        f = AIPFile("a.png", src, src.stat().st_size, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        derivative = normalizer.normalize(f, rule, tmp_path / "work")

        assert derivative.relative_path == "a-preservation.tiff"
        assert derivative.puid_out == conversion_registry.TIFF_PUID
        assert "Pillow" in derivative.tool_name
        assert derivative.sha256 == sha256_of(derivative.path)
        assert src.read_bytes(), "原本は残っている"
