"""Archivematica の FPR 書式（rules.json）の規則。

利用者の規則を FPR の JSON で書けるようにしたのは、同じファイルと同じ変換スクリプトを
Archivematica にも読ませるため（core/fpr.py の冒頭）。ここでは、読み込み・警告・
印の置き換え・実際の変換（短い bash スクリプト）・検証・eventDetail を固定する。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from archival_packager.core import conversion_registry, fpr, normalizer, rule_table
from archival_packager.core.aip_models import (
    AIPFile,
    AIPPipelineError,
    DerivativePurpose,
    Executor,
    RuleSource,
)

needs_bash = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="bash が要る"
)

FMT_IN = "00000000-0000-0000-0000-0000000000a1"
FMT_OUT = "00000000-0000-0000-0000-0000000000a2"
TOOL = "00000000-0000-0000-0000-0000000000b1"
CMD = "00000000-0000-0000-0000-0000000000c1"
VERIFY = "00000000-0000-0000-0000-0000000000c2"
DETAIL = "00000000-0000-0000-0000-0000000000c3"
RULE = "00000000-0000-0000-0000-0000000000d1"

COPY = 'cp "%fileFullName%" "%outputLocation%"'


def fixture(
    *,
    command: str = COPY,
    script_type: str = "bashScript",
    output_location: str = "%outputDirectory%%prefix%%fileName%%postfix%.txt",
    verification: str | None = None,
    event_detail: str | None = None,
    purpose: str = "preservation",
    rule_enabled: bool = True,
    command_enabled: bool = True,
    format_uuid: str = FMT_IN,
) -> list[dict]:
    records: list[dict] = [
        {"model": "fpr.fptool", "fields": {"uuid": TOOL, "description": "TestTool", "version": "1",
                                            "enabled": True, "slug": "t"}},
        {"model": "fpr.formatversion", "fields": {"uuid": FMT_IN, "pronom_id": "x-fmt/9001",
                                                   "description": "Test In"}},
        {"model": "fpr.formatversion", "fields": {"uuid": FMT_OUT, "pronom_id": "x-fmt/9002",
                                                   "description": "Test Out"}},
        {"model": "fpr.fpcommand", "fields": {
            "uuid": CMD, "enabled": command_enabled, "tool": TOOL, "description": "Copy it",
            "command": command, "script_type": script_type, "output_location": output_location,
            "output_format": FMT_OUT, "command_usage": "normalization",
            "verification_command": VERIFY if verification is not None else None,
            "event_detail_command": DETAIL if event_detail is not None else None}},
        {"model": "fpr.fprule", "fields": {"uuid": RULE, "enabled": rule_enabled, "purpose": purpose,
                                           "format": format_uuid, "command": CMD}},
    ]
    if verification is not None:
        records.append({"model": "fpr.fpcommand", "fields": {
            "uuid": VERIFY, "enabled": True, "tool": TOOL, "description": "v", "command": verification,
            "script_type": "bashScript", "command_usage": "verification"}})
    if event_detail is not None:
        records.append({"model": "fpr.fpcommand", "fields": {
            "uuid": DETAIL, "enabled": True, "tool": TOOL, "description": "d", "command": event_detail,
            "script_type": "bashScript", "command_usage": "event_detail"}})
    return records


def parse(records) -> tuple[list, list[str]]:
    return fpr.parse(json.dumps(records))


@pytest.fixture
def original(tmp_path) -> AIPFile:
    src = tmp_path / "in" / "memo 1.docx"
    src.parent.mkdir()
    src.write_text("hello", encoding="utf-8")
    return AIPFile(relative_path="memo 1.docx", absolute_path=src, size_bytes=5, uuid="file-uuid",
                   puid="x-fmt/9001")


class TestParse:
    def test_a_rule_is_built_from_the_records(self):
        rules, warnings = parse(fixture())
        assert warnings == []
        (rule,) = rules
        assert rule.rule_id == RULE
        assert rule.puid_in == "x-fmt/9001" and rule.puid_out == "x-fmt/9002"
        assert rule.executor is Executor.FPR and rule.source is RuleSource.USER
        assert rule.tool == "TestTool" and rule.out_extension == "txt"
        assert rule.format_name_out == "Test Out"
        assert rule.fpr.command.uuid == CMD and rule.fpr.description == "Copy it"

    def test_bundled_table_resolves_archivematica_format_uuids(self):
        """fixture に fpr.formatversion が無くても、Archivematica の形式 UUID を PUID に直せること。"""
        records = [r for r in fixture() if r["model"] != "fpr.formatversion"]
        for r in records:
            if r["model"] == "fpr.fprule":
                r["fields"]["format"] = "477c4b05-06eb-4ed5-913b-752e6d71845e"  # Word 2007+
        rules, warnings = parse(records)
        assert warnings == [] and rules[0].puid_in == "fmt/412"

    def test_the_fixture_own_formats_win_over_the_bundled_table(self):
        records = fixture(format_uuid="477c4b05-06eb-4ed5-913b-752e6d71845e")
        records.append({"model": "fpr.formatversion", "fields": {
            "uuid": "477c4b05-06eb-4ed5-913b-752e6d71845e", "pronom_id": "x-fmt/1234"}})
        rules, _ = parse(records)
        assert rules[0].puid_in == "x-fmt/1234"

    def test_broken_json_is_a_warning_not_an_exception(self):
        rules, warnings = fpr.parse("[{")
        assert rules == [] and "書式が壊れています" in warnings[0]

    def test_not_a_list_is_a_warning(self):
        rules, warnings = fpr.parse("{}")
        assert rules == [] and warnings

    def test_unknown_format_uuid_skips_the_rule(self):
        rules, warnings = parse(fixture(format_uuid="ffffffff-0000-0000-0000-000000000000"))
        assert rules == []
        assert "PUID に直せません" in warnings[0] and RULE in warnings[0]

    def test_unsupported_script_type_skips_the_rule(self):
        rules, warnings = parse(fixture(script_type="pythonScript"))
        assert rules == [] and "pythonScript" in warnings[0]

    def test_no_bash_skips_a_bash_script(self, monkeypatch):
        monkeypatch.setattr(fpr, "bash_available", lambda: False)
        rules, warnings = parse(fixture())
        assert rules == [] and "bash" in warnings[0]

    def test_a_plain_command_needs_no_bash(self, monkeypatch):
        monkeypatch.setattr(fpr, "bash_available", lambda: False)
        rules, warnings = parse(fixture(script_type="command", command="cp %fileFullName% %outputLocation%"))
        assert warnings == [] and len(rules) == 1

    def test_unknown_placeholder_warns_but_keeps_the_rule(self):
        rules, warnings = parse(fixture(command='cp "%fileFullNam%" "%outputLocation%"'))
        assert len(rules) == 1
        assert any("%fileFullNam%" in w for w in warnings)

    def test_disabled_rule_is_silently_ignored(self):
        assert parse(fixture(rule_enabled=False)) == ([], [])

    def test_other_purposes_are_silently_ignored(self):
        """Archivematica 用に同じファイルへ書かれた thumbnail などを咎めない。"""
        assert parse(fixture(purpose="thumbnail")) == ([], [])

    def test_access_purpose_is_loaded_but_distinct(self):
        (rule,), _ = parse(fixture(purpose="access"))
        assert rule.purpose is DerivativePurpose.ACCESS

    def test_disabled_command_warns(self):
        rules, warnings = parse(fixture(command_enabled=False))
        assert rules == [] and "enabled" in warnings[0]

    def test_output_must_stay_under_the_output_directory(self):
        """出力先を外に向けられると、無関係なファイルを派生物として取り込んでしまう。"""
        rules, warnings = parse(fixture(output_location="/etc/passwd.txt"))
        assert rules == [] and "%outputDirectory%" in warnings[0]


class TestSubstitution:
    def test_values_follow_archivematica(self, tmp_path):
        v = fpr.replacements(Path("/data/a b/memo.tar.docx"), output_dir=tmp_path, postfix="-x")
        assert v["%fileFullName%"] == "/data/a b/memo.tar.docx"
        assert v["%fileName%"] == "memo.tar"
        assert v["%fileExtension%"] == "docx" and v["%fileExtensionWithDot%"] == ".docx"
        assert v["%fileDirectory%"] == "/data/a b"
        assert v["%outputDirectory%"] == str(tmp_path) + "/" or sys.platform == "win32"
        assert v["%prefix%"] == "" and v["%postfix%"] == "-x"

    def test_one_pass_does_not_rewrite_a_file_name_that_looks_like_a_token(self):
        """ファイル名 "%prefix%.docx" の中身を、あとの置き換えで書き換えてはいけない。"""
        values = {"%fileName%": "%prefix%", "%prefix%": "P"}
        assert fpr.substitute("%fileName%-%prefix%", values) == "%prefix%-P"

    def test_unknown_tokens_are_left_alone(self):
        assert fpr.substitute("a %nope% b", {}) == "a %nope% b"

    def test_event_detail_output_is_parsed(self):
        assert fpr.parse_event_detail('program="LibreOffice"; version="LibreOffice 24.8.1"\n') == (
            "LibreOffice", "LibreOffice 24.8.1")
        assert fpr.parse_event_detail("nothing") == ("", "")


@needs_bash
class TestNormalize:
    def rule(self, **kw):
        rules, warnings = parse(fixture(**kw))
        assert warnings == [], warnings
        return rules[0]

    def test_a_bash_script_produces_the_derivative(self, original, tmp_path):
        work = tmp_path / "work"
        d = normalizer.normalize(original, self.rule(), work)
        assert d.path.read_text(encoding="utf-8") == "hello"
        assert d.path.parent == work and d.path.suffix == ".txt"
        assert d.relative_path == "memo 1-preservation.txt"
        assert d.rule_id == RULE and d.rule_source == "user" and d.fpr_command_id == CMD
        # 作業フォルダに、スクリプトの一時物（fpr-*）を残さない
        assert [p.name for p in work.iterdir()] == [d.path.name]
        assert original.absolute_path.read_text(encoding="utf-8") == "hello"  # 原本は無傷

    def test_missing_output_is_a_failure(self, original, tmp_path):
        with pytest.raises(AIPPipelineError, match="出力が生成されませんでした"):
            normalizer.normalize(original, self.rule(command="true"), tmp_path / "w")

    def test_nonzero_exit_is_a_failure_with_stderr(self, original, tmp_path):
        with pytest.raises(AIPPipelineError, match="boom"):
            normalizer.normalize(original, self.rule(command="echo boom >&2; exit 3"), tmp_path / "w")

    def test_a_failing_verification_fails_the_derivative(self, original, tmp_path):
        rule = self.rule(verification="echo not a pdf >&2; exit 1")
        with pytest.raises(AIPPipelineError, match="検証の命令が失敗"):
            normalizer.normalize(original, rule, tmp_path / "w")

    def test_a_passing_verification_sees_the_output_location(self, original, tmp_path):
        rule = self.rule(verification='test -s "%outputLocation%"')
        assert normalizer.normalize(original, rule, tmp_path / "w").path.is_file()

    def test_event_detail_fills_tool_name_and_version(self, original, tmp_path):
        rule = self.rule(event_detail="echo 'program=\"Tester\"; version=\"Tester 1.2\"'")
        d = normalizer.normalize(original, rule, tmp_path / "w")
        assert (d.tool_name, d.tool_version) == ("Tester", "Tester 1.2")
        line = normalizer.event_detail(d)
        assert line.startswith(f'ArchivematicaFPRCommandID="{CMD}"; rule="{RULE}"')
        assert 'program="Tester"' in line and 'version="Tester 1.2"' in line

    def test_failed_event_detail_falls_back_to_unknown(self, original, tmp_path):
        d = normalizer.normalize(original, self.rule(event_detail="exit 1"), tmp_path / "w")
        assert d.tool_name == "TestTool" and d.tool_version == ""
        assert 'version="unknown"' in normalizer.event_detail(d)

    def test_a_command_type_runs_without_a_shell(self, original, tmp_path):
        """script_type=command はシェルを通さない（Archivematica と同じ）。&& は効かない。"""
        rule = self.rule(script_type="command", command="cp %fileFullName% %outputLocation%")
        # パスに空白（"memo 1.docx"）があるので、分割されて失敗する。Archivematica と同じ動き。
        with pytest.raises(AIPPipelineError):
            normalizer.normalize(original, rule, tmp_path / "w")

    def test_a_command_type_works_for_plain_paths(self, tmp_path):
        src = tmp_path / "plain.docx"
        src.write_text("x", encoding="utf-8")
        f = AIPFile(relative_path="plain.docx", absolute_path=src, size_bytes=1, uuid="u", puid="x-fmt/9001")
        rules, _ = parse(fixture(script_type="command", command="cp %fileFullName% %outputLocation%"))
        d = normalizer.normalize(f, rules[0], tmp_path / "w")
        assert d.path.read_text(encoding="utf-8") == "x"
        assert d.command_line.startswith("cp ")

    def test_output_escaping_the_work_folder_is_refused(self, original, tmp_path):
        rule = self.rule(output_location="%outputDirectory%../escape.txt")
        with pytest.raises(AIPPipelineError, match="作業フォルダの外"):
            normalizer.normalize(original, rule, tmp_path / "w")


class TestLoading:
    def write(self, tmp_path, name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_rules_json_is_read_next_to_rules_toml(self, tmp_path):
        text = json.dumps(fixture())
        self.write(tmp_path, "rules.json", text)
        table = rule_table.load(tmp_path / "rules.toml")
        assert table.rule_for("x-fmt/9001", DerivativePurpose.PRESERVATION).rule_id == RULE
        assert table.json_document == text  # 原文のまま

    def test_broken_json_is_not_embedded(self, tmp_path):
        self.write(tmp_path, "rules.json", "[{")
        table = rule_table.load(tmp_path / "rules.toml")
        assert table.json_document == ""
        assert any("書式が壊れています" in w for w in table.warnings)

    def test_json_wins_over_toml_on_the_same_puid_and_warns(self, tmp_path):
        self.write(tmp_path, "rules.json", json.dumps(fixture()))
        self.write(tmp_path, "rules.toml", """
[[rule]]
id = "from-toml"
puid_in = ["x-fmt/9001"]
executor = "command"
tool = "x"
args = ["{in}", "{out}"]
out_extension = "txt"
""")
        table = rule_table.load(tmp_path / "rules.toml")
        assert table.rule_for("x-fmt/9001", DerivativePurpose.PRESERVATION).rule_id == RULE
        assert any("from-toml" in w and "既に" in w for w in table.warnings)

    def test_rules_json_overrides_a_builtin_rule(self, tmp_path):
        records = fixture()
        for r in records:
            if r["model"] == "fpr.formatversion" and r["fields"]["uuid"] == FMT_IN:
                r["fields"]["pronom_id"] = "fmt/11"  # 組み込みの PNG → TIFF
        self.write(tmp_path, "rules.json", json.dumps(records))
        table = rule_table.load(tmp_path / "rules.toml")
        assert table.rule_for("fmt/11", DerivativePurpose.PRESERVATION).rule_id == RULE
        assert table.warnings == []

    def test_absent_files_mean_builtin_only(self, tmp_path):
        table = rule_table.load(tmp_path / "rules.toml")
        assert table.json_document == "" and table.warnings == []

    def test_registry_exposes_the_document(self, tmp_path):
        self.write(tmp_path, "rules.json", json.dumps(fixture()))
        conversion_registry.reload(tmp_path / "rules.toml")
        try:
            assert json.loads(conversion_registry.json_document())
        finally:
            conversion_registry.reload(tmp_path / "absent" / "rules.toml")


def test_the_format_table_is_generated_from_identifiers_only():
    from archival_packager.core import fpr_formats

    assert fpr_formats.FORMATS["477c4b05-06eb-4ed5-913b-752e6d71845e"] == "fmt/412"
    assert "243867858da3" in fpr_formats.SOURCE  # どの版から作ったかが残っている
