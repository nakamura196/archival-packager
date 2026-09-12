"""完全性確認と正規化のテスト。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from archival_packager.core import conversion_registry, fixity, image_normalize, normalizer
from archival_packager.core.aip_models import (
    AIPFile,
    AIPPipelineError,
    DerivativePurpose,
    Executor,
    FixityOutcome,
    NormalizationRule,
    RuleSource,
)
from archival_packager.core.checksums import sha256_of


@pytest.fixture(autouse=True)
def builtin_rules_only(tmp_path):
    """開発機に置いてある本物の rules.toml でテストの通り方が変わらないようにする。

    規則表は利用者の環境から読む。テストが環境に左右されると、落ちたときに
    アプリの不具合なのか手元の設定なのかを切り分けられない。
    """
    conversion_registry.reload(tmp_path / "no-such-rules.toml")
    yield
    conversion_registry.reload(tmp_path / "no-such-rules.toml")


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

    def test_every_rule_carries_an_identifier(self):
        """表に載る規則には必ず識別子を付けること。

        識別子は PREMIS の eventDetail にそのまま書かれる（Archivematica の
        FPRCommandID に相当する）。付け忘れると、その AIP だけ
        「どの規則で作られたか」が空欄のまま世に出る。**出てしまってからでは
        直せない**（作り直さない限り、その AIP の記録は欠けたまま）。
        """
        for puid in ("fmt/11", "fmt/41", "fmt/3", "fmt/116", "fmt/124", "fmt/122"):
            rule = conversion_registry.rule_for(puid, DerivativePurpose.PRESERVATION)
            assert rule is not None and rule.rule_id, f"{puid} の規則に識別子が無い"

    def test_identifiers_do_not_change(self):
        """**値を書き換えないこと。** 過去に作った AIP の PREMIS には、
        ここに書かれている文字列がそのまま入っている。改名すると、その AIP を
        読んだ人が今の規則表と突き合わせられなくなる。

        リテラルで書くのは、定数の中身を書き換えたときにここが落ちるため
        （定数どうしを比べても、一緒に変わってしまえば気づけない）。
        """
        assert (
            conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION).rule_id
            == "image-to-tiff"
        )
        assert (
            conversion_registry.rule_for("fmt/124", DerivativePurpose.PRESERVATION).rule_id
            == "postscript-to-pdf"
        )


class TestRuleSourceIsRecorded:
    """組み込みの規則か、利用者が足した規則かが、記録から分かること。

    **同じ資料から別の組織が別の AIP を作ったとき、違いの原因が
    「表を足したから」なのかどうかを、後から見た人が判断できる必要がある。**
    """

    def test_builtin_rules_say_so(self, tmp_path):
        from PIL import Image

        src = tmp_path / "a.png"
        Image.new("RGB", (4, 4)).save(src)
        f = AIPFile("a.png", src, src.stat().st_size, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        derivative = normalizer.normalize(f, rule, tmp_path / "work")

        assert derivative.rule_source == "builtin"
        assert 'ruleSource="builtin"' in normalizer.event_detail(derivative)

    def test_user_rules_say_so(self, tmp_path, fake_tool, monkeypatch):
        fake = fake_tool(name="conv", output_text="out")
        (tmp_path / "rules.toml").write_text(
            """
[[rule]]
id = "png-to-jp2"
puid_in = ["fmt/11"]
executor = "command"
tool = "conv"
args = ["{in}", "{out}"]
out_extension = "jp2"
""",
            encoding="utf-8",
        )
        conversion_registry.reload(tmp_path / "rules.toml")
        monkeypatch.setattr(normalizer, "locate", lambda tool: fake)

        src = tmp_path / "a.png"
        src.write_bytes(b"x")
        f = AIPFile("a.png", src, 1, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        assert rule.rule_id == "png-to-jp2", "利用者の規則が組み込みを上書きする"

        derivative = normalizer.normalize(f, rule, tmp_path / "work")
        detail = normalizer.event_detail(derivative)
        assert derivative.rule_source == "user"
        assert 'ruleSource="user"' in detail
        # 識別子そのものは飾らない。過去の AIP に書かれた値と突き合わせるため。
        assert 'rule="png-to-jp2"' in detail

    def test_the_identifier_is_not_decorated(self, tmp_path):
        """rule="image-to-tiff(builtin)" のように識別子へ混ぜない。

        識別子は過去の AIP にそのまま書き込まれている文字列で、後年それと
        突き合わせるためにある。装飾を足すと、同じ規則で作った古い AIP と
        新しい AIP で値が食い違う。
        """
        from PIL import Image

        src = tmp_path / "a.png"
        Image.new("RGB", (4, 4)).save(src)
        f = AIPFile("a.png", src, src.stat().st_size, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        detail = normalizer.event_detail(normalizer.normalize(f, rule, tmp_path / "work"))
        assert 'rule="image-to-tiff"' in detail


class TestBuiltinProcessingIsNotReachableFromTheTable:
    def test_dispatch_is_by_executor_not_by_tool_name(self, tmp_path, fake_tool, monkeypatch):
        """利用者が tool = "pillow" と書いた外部コマンドの規則を、
        アプリ内蔵の画像変換と取り違えないこと。

        道具の名前で分岐していると、名前が一致しただけで内蔵処理が動く。
        内蔵処理を呼ぶ道は executor = "builtin" だけにする。
        """
        fake = fake_tool(name="pillow", output_text="out")
        monkeypatch.setattr(normalizer, "locate", lambda tool: fake)

        rule = NormalizationRule(
            puid_in="fmt/11", purpose=DerivativePurpose.PRESERVATION,
            tool=image_normalize.TOOL, args=["{in}", "{out}"], out_extension="tiff",
            executor=Executor.COMMAND, source=RuleSource.USER,
        )
        src = tmp_path / "a.png"
        src.write_bytes(b"not a png at all")  # 内蔵変換なら壊れた画像として失敗する
        f = AIPFile("a.png", src, src.stat().st_size, "u1")

        derivative = normalizer.normalize(f, rule, tmp_path / "work")
        assert derivative.path.read_bytes().strip() == b"out", "外部コマンドが動いていない"

    def test_builtin_executor_accepts_only_the_processing_it_knows(self, tmp_path):
        """組み込みの表にしか現れない道なので、ここに来るのはアプリの不具合。

        黙って画像変換にかけると、規則が言っていない変換が行われる。
        """
        rule = NormalizationRule(
            puid_in="fmt/141", purpose=DerivativePurpose.PRESERVATION,
            tool="something-else", args=[], out_extension="tiff",
            executor=Executor.BUILTIN,
        )
        src = tmp_path / "a.wav"
        src.write_bytes(b"x")
        with pytest.raises(AIPPipelineError):
            normalizer.normalize(AIPFile("a.wav", src, 1, "u1"), rule, tmp_path / "work")


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
        # 道具の呼び名と版は別の欄に持つ。eventDetail で
        # program="pillow"; version="Pillow ..." と書き分けるため。
        assert derivative.tool_name == image_normalize.TOOL
        assert "Pillow" in derivative.tool_version
        assert "libtiff" in derivative.tool_version
        assert derivative.sha256 == sha256_of(derivative.path)
        assert src.read_bytes(), "原本は残っている"

    def test_event_detail_carries_rule_program_and_version(self, tmp_path):
        """アプリ内変換でも、規則・道具・版の 3 つが揃うこと。

        同梱している Pillow は版が分かりきっていると思いがちだが、
        **AIP を読む人はアプリの配布物を持っていない。** METS だけで
        判断できる必要がある。
        """
        src = self._png(tmp_path / "a.png")
        f = AIPFile("a.png", src, src.stat().st_size, "u1")
        rule = conversion_registry.rule_for("fmt/11", DerivativePurpose.PRESERVATION)
        detail = normalizer.event_detail(normalizer.normalize(f, rule, tmp_path / "work"))

        assert 'rule="image-to-tiff"' in detail
        assert 'program="pillow"' in detail
        assert 'version="Pillow' in detail
        assert detail.count("Pillow") == 1, "同じ版が 2 度出ると別物の話に見える"


class TestToolVersionIsRecorded:
    """外部ツールの版を保存処理記録に残す。

    Ghostscript は AGPL のため同梱しておらず、**利用者の環境にあるものが動く。**
    つまり配布物からは版が決まらない。版を記録していないと、あとから
    「この PDF は何で作られたのか」「同じものを作り直せるのか」を判断できない。
    画像変換（Pillow）は版まで記録していたのに、いちばん記録すべき gs だけが
    抜けていた。
    """

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        # 版は 1 回だけ聞いて使い回す（モジュールに溜める）。
        # テスト間で持ち越すと、別のテストが仕込んだ版を見てしまう。
        normalizer._version_cache.clear()
        yield
        normalizer._version_cache.clear()

    def _ps_rule(self):
        return conversion_registry.rule_for("fmt/124", DerivativePurpose.PRESERVATION)

    def _fake_gs(self, monkeypatch, tmp_path, *, version_stdout="9.99.9", version_fails=False):
        """gs の代わりに、版を答えて PDF らしきものを書く偽物を仕込む。

        本物の gs があるかどうかでテストの通り方が変わってはいけない
        （CI にも開発機にも無いことがある）。呼ばれた引数も記録して返す。
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **_kw):
            calls.append(list(cmd))
            if "--version" in cmd:
                if version_fails:
                    raise OSError("gs が起動できない")
                return SimpleNamespace(returncode=0, stdout=version_stdout, stderr="")
            out = next(a.split("=", 1)[1] for a in cmd if a.startswith("-sOutputFile="))
            Path(out).write_bytes(b"%PDF-1.7\n")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(normalizer, "locate", lambda tool: tmp_path / "fake-gs")
        monkeypatch.setattr(normalizer.bundled, "ensure_executable", lambda _p: None)
        monkeypatch.setattr(normalizer.subprocess, "run", fake_run)
        return calls

    def _run(self, tmp_path):
        src = tmp_path / "a.ps"
        src.write_bytes(b"%!PS\n")
        f = AIPFile("a.ps", src, src.stat().st_size, "u1")
        return src, normalizer.normalize(f, self._ps_rule(), tmp_path / "work")

    def test_ghostscript_version_reaches_the_event_detail(self, tmp_path, monkeypatch):
        self._fake_gs(monkeypatch, tmp_path)
        _src, derivative = self._run(tmp_path)

        assert derivative.tool_version == "Ghostscript 9.99.9", (
            "gs は版だけを返すので、何の版かを添えて記録する"
        )
        assert 'version="Ghostscript 9.99.9"' in normalizer.event_detail(derivative)

    def test_missing_version_does_not_stop_the_conversion(self, tmp_path, monkeypatch):
        """版を聞けないことは変換の失敗ではない。

        gs が古くて --version を解さない、起動はするが壊れている、という
        事情で AIP が作れなくなっては本末転倒。記録が薄くなるだけにする。
        """
        self._fake_gs(monkeypatch, tmp_path, version_fails=True)
        _src, derivative = self._run(tmp_path)

        assert derivative.path.is_file(), "変換自体は行われる"
        assert derivative.tool_version == ""
        assert 'version="unknown"' in normalizer.event_detail(derivative), (
            "空欄だと「聞き忘れた」のか「答えなかった」のか読み取れない"
        )

    def test_version_is_asked_only_once(self, tmp_path, monkeypatch):
        """1 回の移管で何十件も変換する。そのたびにプロセスを起こさない。"""
        calls = self._fake_gs(monkeypatch, tmp_path)
        for n in range(3):
            src = tmp_path / f"{n}.ps"
            src.write_bytes(b"%!PS\n")
            normalizer.normalize(
                AIPFile(f"{n}.ps", src, 5, f"u{n}"), self._ps_rule(), tmp_path / "work"
            )

        assert sum(1 for c in calls if "--version" in c) == 1
        assert sum(1 for c in calls if "--version" not in c) == 3

    def test_absent_gs_is_still_a_clean_failure(self, tmp_path, monkeypatch):
        """gs がそもそも無い環境でも、版を聞きに行って落ちたりしないこと。"""
        monkeypatch.setattr(normalizer, "locate", lambda tool: None)
        src = tmp_path / "a.ps"
        src.write_bytes(b"%!PS\n")
        with pytest.raises(AIPPipelineError) as exc:
            normalizer.normalize(
                AIPFile("a.ps", src, 5, "u1"), self._ps_rule(), tmp_path / "work"
            )
        assert "変換ツールが見つかりません" in exc.value.message

    def test_the_original_is_untouched(self, tmp_path, monkeypatch):
        """**このアプリの最重要の性質。** 版を聞く処理を足したあとも、
        原本には一切書き込まないこと。"""
        self._fake_gs(monkeypatch, tmp_path)
        src = tmp_path / "a.ps"
        src.write_bytes(b"%!PS original bytes\n")
        before = sha256_of(src)
        mtime = src.stat().st_mtime

        derivative = normalizer.normalize(
            AIPFile("a.ps", src, src.stat().st_size, "u1"),
            self._ps_rule(),
            tmp_path / "work",
        )

        assert sha256_of(src) == before, "原本のバイト列が変わっている"
        assert src.stat().st_mtime == mtime, "原本に書き込んでいる"
        assert derivative.path != src
