"""変換規則の表（rule_table）。

ここで確かめたいのは「規則を足せること」だけではない。

- **組み込みの規則が、利用者の規則とまったく同じ道を通ること。**
  組み込みが別の道を通っていると、利用者向けの道は誰も使わないまま壊れる。
- **壊れた表でアプリが起動しなくなっていないこと。**
  規則表は補助的な設定であって、これが読めないせいで資料を受け入れられなく
  なるのは本末転倒。
- **表から実行できることの範囲が広がっていないこと。**
  読み込むのはデータだけで、動くのは利用者の環境にある外部コマンドだけ。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archival_packager.core import conversion_registry, rule_table
from archival_packager.core.aip_models import (
    DerivativePurpose,
    Executor,
    NormalizationRule,
    RuleSource,
)

PRESERVATION = DerivativePurpose.PRESERVATION

USER_RULE = """
[[rule]]
id = "wav-to-flac"
puid_in = ["fmt/141", "fmt/142"]
purpose = "preservation"
executor = "command"
tool = "flac"
args = ["--best", "-o", "{out}", "{in}"]
puid_out = "fmt/279"
format_name_out = "Free Lossless Audio Codec"
out_extension = "flac"
"""


@pytest.fixture
def absent(tmp_path: Path) -> Path:
    """利用者の表が無い状態。**既定では何も設定しなくても動くこと。**"""
    return tmp_path / "no-such-rules.toml"


def write_user(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    return path


class TestBuiltinGoesThroughTheSameDoor:
    """組み込みの 2 規則も、利用者が書くのと同じ TOML から読む。

    別の道を通していると、利用者向けの読み込みと検証は誰にも使われないまま
    壊れていく。同じ道なら、起動のたび・テストのたびに必ず動く。
    """

    def test_builtin_table_parses_with_the_user_parser(self):
        entries, warnings = rule_table.parse(
            rule_table.BUILTIN_TOML, source=RuleSource.BUILTIN
        )
        assert warnings == [], "組み込みの表が検証を通らないのはアプリの不具合"
        assert {e.rule_id for e in entries} == {"image-to-tiff", "postscript-to-pdf"}

    def test_builtin_rules_have_the_same_type_as_user_rules(self, tmp_path):
        table = rule_table.load(write_user(tmp_path, USER_RULE))
        builtin = table.rule_for("fmt/11", PRESERVATION)
        user = table.rule_for("fmt/141", PRESERVATION)
        assert isinstance(builtin, NormalizationRule)
        assert isinstance(user, NormalizationRule)
        assert builtin.source is RuleSource.BUILTIN
        assert user.source is RuleSource.USER

    def test_the_two_existing_rules_are_unchanged(self, absent):
        """表に移したあとも、これまでと同じものが引けること。"""
        table = rule_table.load(absent)

        tiff = table.rule_for("fmt/11", PRESERVATION)
        assert tiff.rule_id == "image-to-tiff"
        assert tiff.executor is Executor.BUILTIN
        assert tiff.tool == "pillow"
        assert tiff.args == [], "アプリ内変換は外部プロセスの引数を持たない"
        assert tiff.puid_out == "fmt/353"
        assert tiff.format_name_out == "Tagged Image File Format"
        assert tiff.out_extension == "tiff"

        ps = table.rule_for("fmt/124", PRESERVATION)
        assert ps.rule_id == "postscript-to-pdf"
        assert ps.executor is Executor.COMMAND
        assert ps.tool == "gs"
        assert ps.args == [
            "-dNOPAUSE", "-dBATCH", "-dSAFER",
            "-sDEVICE=pdfwrite", "-sOutputFile={out}", "{in}",
        ]
        assert ps.puid_out == "fmt/276"
        assert ps.out_extension == "pdf"

    def test_every_puid_of_the_old_registry_still_resolves(self, absent):
        """PUID を取りこぼしていないこと（表に写すときに落ちやすい）。"""
        table = rule_table.load(absent)
        images = [
            "fmt/11", "fmt/12", "fmt/13", "fmt/935",
            "fmt/41", "fmt/42", "fmt/43", "fmt/44",
            "x-fmt/398", "x-fmt/390", "x-fmt/391",
            "fmt/3", "fmt/4",
            "fmt/116", "fmt/117", "fmt/119", "x-fmt/270",
        ]
        postscript = [
            "fmt/124", "fmt/501",
            "x-fmt/91", "x-fmt/406", "x-fmt/407", "x-fmt/408",
            "fmt/122", "fmt/123",
        ]
        for puid in images:
            assert table.rule_for(puid, PRESERVATION).rule_id == "image-to-tiff", puid
        for puid in postscript:
            assert table.rule_for(puid, PRESERVATION).rule_id == "postscript-to-pdf", puid

    def test_works_without_any_user_table(self, absent):
        table = rule_table.load(absent)
        assert table.warnings == []
        assert table.rule_for("fmt/11", PRESERVATION) is not None


class TestUserRules:
    def test_a_user_rule_is_added(self, tmp_path):
        table = rule_table.load(write_user(tmp_path, USER_RULE))
        rule = table.rule_for("fmt/141", PRESERVATION)
        assert rule.rule_id == "wav-to-flac"
        assert rule.tool == "flac"
        assert rule.args == ["--best", "-o", "{out}", "{in}"]
        assert rule.out_extension == "flac"
        # 表の 1 行が複数の PUID を受け持つ（同じ変換を並べて書かせない）。
        assert table.rule_for("fmt/142", PRESERVATION).rule_id == "wav-to-flac"

    def test_user_rule_wins_over_builtin(self, tmp_path):
        """自分の運用に合わせて上書きできないと、表を書ける意味が半分になる。"""
        path = write_user(tmp_path, """
[[rule]]
id = "png-to-jpeg2000"
puid_in = ["fmt/11"]
executor = "command"
tool = "opj_compress"
args = ["-i", "{in}", "-o", "{out}"]
out_extension = "jp2"
""")
        table = rule_table.load(path)
        rule = table.rule_for("fmt/11", PRESERVATION)
        assert rule.rule_id == "png-to-jpeg2000"
        assert rule.source is RuleSource.USER
        # 上書きされていない PUID は組み込みのまま。
        assert table.rule_for("fmt/41", PRESERVATION).rule_id == "image-to-tiff"

    def test_purpose_defaults_to_preservation(self, tmp_path):
        path = write_user(tmp_path, """
[[rule]]
id = "no-purpose"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{in}", "{out}"]
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is not None


class TestBrokenTablesDoNotStopTheApp:
    """**壊れた表で起動できなくなってはならない。**

    規則表は補助的な設定である。書き間違いのせいで資料を受け入れられなく
    なるくらいなら、その規則だけを捨てて動き続けるほうがよい。ただし
    黙って捨てない（なぜ効かないかは利用者にしか直せない）。
    """

    def test_syntax_error_leaves_the_builtin_rules_working(self, tmp_path):
        path = write_user(tmp_path, "[[rule]\nid = 壊れている")
        table = rule_table.load(path)
        assert table.rule_for("fmt/11", PRESERVATION).rule_id == "image-to-tiff"
        assert any("書式が壊れています" in w for w in table.warnings)

    def test_one_bad_rule_does_not_take_down_the_good_ones(self, tmp_path):
        path = write_user(tmp_path, USER_RULE + """
[[rule]]
id = "broken"
puid_in = ["fmt/999"]
executor = "command"
tool = "x"
args = ["{tmp}", "{out}"]
out_extension = "x"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is not None, "隣の規則まで捨てない"
        assert table.rule_for("fmt/999", PRESERVATION) is None
        assert any("{tmp}" in w for w in table.warnings)

    def test_unreadable_file_is_a_warning(self, tmp_path):
        """読めないファイル（壊れた文字コード等）でも起動を妨げない。"""
        path = tmp_path / "rules.toml"
        path.write_bytes(b"\xff\xfe\x00invalid utf-8")
        table = rule_table.load(path)
        assert table.rule_for("fmt/11", PRESERVATION) is not None
        assert table.warnings, "読めなかったことは伝える"

    def test_reasons_are_written_in_japanese(self, tmp_path):
        """落とした理由は、利用者が読んで直せる言葉であること。

        表を書くのは情報システムの専門家ではない。"invalid executor" では
        何をどう直せばよいか分からず、結局アプリを使うのをやめることになる。
        """
        path = write_user(tmp_path, """
[[rule]]
id = "no-tool"
puid_in = ["fmt/141"]
executor = "command"
args = ["{in}", "{out}"]
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.warnings
        for w in table.warnings:
            assert any("\u3040" <= ch <= "\u30ff" or "\u4e00" <= ch <= "\u9fff" for ch in w), w
            assert "no-tool" in w, "どの規則の話かが分かること"


class TestWhatTheTableMayNotDo:
    """表から実行できることの範囲。**ここが広がると配布の前提が崩れる。**"""

    def test_user_cannot_call_the_builtin_executor(self, tmp_path):
        """内蔵処理を外から呼ばせない。

        呼べてしまうと、審査を通した配布物の振る舞いが、利用者の書いた表で
        変わることになる。表はあくまで「外部コマンドの呼び方」に留める。
        """
        path = write_user(tmp_path, """
[[rule]]
id = "sneak-in"
puid_in = ["fmt/141"]
executor = "builtin"
tool = "pillow"
args = []
out_extension = "tiff"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None
        assert any("builtin" in w and "指定できません" in w for w in table.warnings)

    def test_builtin_executor_is_still_available_to_the_builtin_table(self):
        entries, warnings = rule_table.parse(
            rule_table.BUILTIN_TOML, source=RuleSource.BUILTIN
        )
        assert warnings == []
        assert any(e.executor is Executor.BUILTIN for e in entries)

    @pytest.mark.parametrize(
        "tool", ["../evil", "/usr/bin/evil", "bin/evil", "..\\evil", "C:\\evil.exe"]
    )
    def test_paths_are_rejected_in_tool(self, tmp_path, tool):
        """tool にパスを書かせない。

        相対パスは「どこから見た相対か」がアプリの作業場所で変わり、
        `..` はその外を指せる。コマンド名だけを受け取る。

        tool を TOML のリテラル文字列（'...'）で書くのは、Windows のパスに
        含まれる \\ が普通の文字列だと打ち消し文字として解釈され、
        **検証にたどり着く前に書式の誤りとして落ちてしまう**ため。
        ここで確かめたいのは検証の側なので、TOML としては正しい形で渡す。
        """
        path = write_user(tmp_path, f"""
[[rule]]
id = "path-tool"
puid_in = ["fmt/141"]
executor = "command"
tool = '{tool}'
args = ["{{in}}", "{{out}}"]
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None
        assert any("path-tool" in w for w in table.warnings)

    def test_unknown_placeholders_are_rejected(self, tmp_path):
        """置き換えられるのは {in} と {out} だけ。

        未知の印を素通しすると、置換されないまま外部コマンドに渡り、
        「{tmp} という名前のファイルが無い」という分かりにくい失敗になる。
        """
        path = write_user(tmp_path, """
[[rule]]
id = "odd-placeholder"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{in}", "{out}", "{home}/x"]
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None
        assert any("{home}" in w for w in table.warnings)

    @pytest.mark.parametrize("args", ['["{in}"]', '["{out}"]', "[]"])
    def test_both_placeholders_are_required(self, tmp_path, args):
        """{out} が無ければどこにも書き出されず、{in} が無ければ原本を読まない。

        どちらも「実行はできるが何も起きない」ので、表の時点で止める。
        """
        path = write_user(tmp_path, f"""
[[rule]]
id = "half"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = {args}
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None

    def test_args_must_be_a_list(self, tmp_path):
        """文字列 1 本で受け取ると、シェルに解釈させたくなる。リストしか取らない。"""
        path = write_user(tmp_path, """
[[rule]]
id = "shellish"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = "{in} > {out}"
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None
        assert any("リスト" in w for w in table.warnings)

    @pytest.mark.parametrize("ext", ["../x", "a/b", "", ".tiff"])
    def test_output_extension_cannot_escape_the_filename(self, tmp_path, ext):
        """拡張子は派生物のファイル名の一部になる。区切りが混じると別の場所に書く。"""
        path = write_user(tmp_path, f"""
[[rule]]
id = "bad-ext"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{{in}}", "{{out}}"]
out_extension = "{ext}"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None

    def test_access_derivatives_are_not_supported_yet(self, tmp_path):
        """利用用（access）の派生物はまだ作れない。

        受け付けてしまうと、保存用として記録された利用用ファイルが AIP に入る。
        記録が実態と違うほうが、機能が無いことより悪い。
        """
        path = write_user(tmp_path, """
[[rule]]
id = "access-jpeg"
puid_in = ["fmt/353"]
purpose = "access"
executor = "command"
tool = "cjpeg"
args = ["{in}", "{out}"]
out_extension = "jpg"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/353", DerivativePurpose.ACCESS) is None
        assert any("preservation" in w for w in table.warnings)

    def test_duplicate_ids_are_rejected(self, tmp_path):
        """id は PREMIS にそのまま書かれる。重複すると、AIP を読んだ人が
        「どちらの規則で作られたか」を決められなくなる。"""
        path = write_user(tmp_path, """
[[rule]]
id = "image-to-tiff"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{in}", "{out}"]
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None, "組み込みと同じ id は使わせない"
        assert table.rule_for("fmt/11", PRESERVATION).rule_id == "image-to-tiff"
        assert any("同じ id" in w for w in table.warnings)

    def test_a_rule_without_an_id_is_rejected(self, tmp_path):
        path = write_user(tmp_path, """
[[rule]]
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{in}", "{out}"]
out_extension = "flac"
""")
        table = rule_table.load(path)
        assert table.rule_for("fmt/141", PRESERVATION) is None
        assert any("id" in w for w in table.warnings)


class TestTheDocumentThatGoesIntoTheAip:
    """AIP に同梱する表。

    Archivematica は PREMIS に FPR の識別子だけを書き、規則の中身は中央の
    登録簿にある。後年その登録簿が引けなくなると、識別子だけ残っても意味を
    失う。表そのものを入れておけば、パッケージ単体で説明が付く。
    """

    def test_contains_the_builtin_rules(self, absent):
        document = rule_table.load(absent).document
        assert "image-to-tiff" in document
        assert "postscript-to-pdf" in document

    def test_contains_the_user_rules(self, tmp_path):
        document = rule_table.load(write_user(tmp_path, USER_RULE)).document
        assert "wav-to-flac" in document
        assert '"--best"' in document, "引数まで残す（何をしたかは引数で決まる）"

    def test_records_the_rules_that_were_ignored(self, tmp_path):
        """**効かなかったという事実自体が来歴である。**

        あるはずの変換が無い理由が、このファイルだけで分かるようにする。
        """
        path = write_user(tmp_path, """
[[rule]]
id = "odd"
puid_in = ["fmt/141"]
executor = "command"
tool = "flac"
args = ["{in}", "{out}", "{tmp}"]
out_extension = "flac"
""")
        document = rule_table.load(path).document
        assert "odd" in document and "{tmp}" in document

    def test_is_valid_toml(self, tmp_path):
        """同梱した .toml が読めないのでは、後から見た人の役に立たない。"""
        import tomllib

        document = rule_table.load(write_user(tmp_path, USER_RULE)).document
        parsed = tomllib.loads(document)
        ids = {r["id"] for r in parsed["rule"]}
        assert {"image-to-tiff", "postscript-to-pdf", "wav-to-flac"} <= ids

    def test_a_broken_user_table_is_not_embedded_verbatim(self, tmp_path):
        """書式ごと壊れた表を貼り付けると、同梱物まで読めなくなる。"""
        import tomllib

        path = write_user(tmp_path, "[[rule]\nこれは TOML ではない")
        document = rule_table.load(path).document
        tomllib.loads(document)  # 例外が出なければよい
        assert "書式が壊れています" in document


class TestRegistryFacade:
    """conversion_registry の外から見た振る舞いは変えない（呼び出し側を壊さない）。"""

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path):
        # 開発機に置いてある本物の rules.toml でテストの通り方が変わらないように、
        # 毎回「利用者の表が無い」状態から始める。
        conversion_registry.reload(tmp_path / "no-such-rules.toml")
        yield
        conversion_registry.reload(tmp_path / "no-such-rules.toml")

    def test_rule_for_still_answers_the_same_way(self):
        assert conversion_registry.rule_for("fmt/11", PRESERVATION).rule_id == "image-to-tiff"
        assert conversion_registry.rule_for("fmt/99999", PRESERVATION) is None
        assert conversion_registry.rule_for(None, PRESERVATION) is None
        assert conversion_registry.rule_for("fmt/11", DerivativePurpose.ACCESS) is None

    def test_the_table_is_read_once(self, tmp_path, monkeypatch):
        """1 回の移管の途中で表が変わらないこと。

        毎回読み直すと、変換の最中に利用者が rules.toml を保存した場合に
        前半と後半で違う規則が効き、1 つの AIP の中に説明の付かない差が残る。
        """
        calls = []
        real_load = rule_table.load
        monkeypatch.setattr(
            rule_table, "load", lambda p=None: (calls.append(p), real_load(p))[1]
        )
        conversion_registry.reload(tmp_path / "no-such-rules.toml")
        for _ in range(3):
            conversion_registry.rule_for("fmt/11", PRESERVATION)
        assert len(calls) == 1
