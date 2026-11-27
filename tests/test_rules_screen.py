"""変換の規則の画面と、画面から開いたアプリでのコマンドの探し方。

なぜ要るか
----------
2026-11-25、LibreOffice で DOCX を PDF/A にする規則を自分で書いて試したところ、
2 つのことで手が止まった。

  1. 規則のファイルは画面からたどれず、書き換えたらアプリを開き直すしかなかった
  2. Finder から開いたアプリは PATH が短く、~/.local/bin に置いた変換用の
     コマンドが見つからなかった（ターミナルから開いたときだけ動いた）

ここでは、画面が規則を読み直せること、ひな形が規則を勝手に効かせないこと、
PATH に無いコマンドも定番の場所から見つかることを押さえる。
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from archival_packager.core import conversion_registry, normalizer, rule_table
from archival_packager.core.aip_models import DerivativePurpose
from archival_packager.ui import app, rules

DOCX_RULE = """
[[rule]]
id = "docx-to-pdfa"
puid_in = ["fmt/412"]
executor = "command"
tool = "ap-test-converter"
args = ["{in}", "{out}"]
puid_out = "fmt/477"
out_extension = "pdf"
"""


@pytest.fixture
def user_file(tmp_path: Path, monkeypatch):
    """利用者の規則のファイルを、一時フォルダに向ける。"""
    path = tmp_path / "data" / rule_table.USER_FILE_NAME
    monkeypatch.setattr(rule_table, "user_table_path", lambda: path)
    conversion_registry.reload(path)
    yield path
    conversion_registry.reload(tmp_path / "absent.toml")


class TestTemplate:
    def test_is_created_when_missing(self, user_file):
        """画面が開くのは rules.json（新しい書式）。rules.toml は読み続けるだけ。"""
        json_path = user_file.parent / rule_table.USER_JSON_NAME
        assert rules.ensure_user_file() == json_path
        assert json_path.read_text(encoding="utf-8") == rules.TEMPLATE
        assert not user_file.exists()

    def test_is_valid_json_with_every_record_disabled(self, user_file):
        """Archivematica の loaddata に渡す物なので、壊れた JSON や有効な例を配らない。"""
        import json

        records = json.loads(rules.TEMPLATE)
        assert {r["model"] for r in records} == {
            "fpr.fptool", "fpr.fpcommand", "fpr.fprule"}
        assert all(r["fields"]["enabled"] is False for r in records)
        assert not any("pk" in r for r in records)

    def test_enables_no_rule_and_raises_no_warning(self, user_file):
        """ひな形を開いただけで規則が効き始めてはいけない（例はすべて無効）。"""
        rules.ensure_user_file()
        table = rule_table.load(user_file)
        assert not any(r.source.value == "user" for r in table.rules.values())
        assert table.warnings == []

    def test_example_works_once_enabled(self, user_file):
        """enabled を true にすれば、そのまま規則として通ること（壊れた例を配らない）。"""
        import json

        records = json.loads(rules.TEMPLATE)
        for r in records:
            r["fields"]["enabled"] = True
        (user_file.parent).mkdir(parents=True)
        (user_file.parent / rule_table.USER_JSON_NAME).write_text(json.dumps(records), encoding="utf-8")
        table = rule_table.load(user_file)
        rule = table.rule_for("fmt/412", DerivativePurpose.PRESERVATION)
        assert rule is not None and rule.puid_out == "fmt/477" and rule.out_extension == "pdf"

    def test_existing_file_is_not_overwritten(self, user_file):
        json_path = user_file.parent / rule_table.USER_JSON_NAME
        json_path.parent.mkdir(parents=True)
        json_path.write_text("[]", encoding="utf-8")
        rules.ensure_user_file()
        assert json_path.read_text(encoding="utf-8") == "[]"


class TestScreen:
    def test_builds_from_the_header(self, user_file):
        page = MagicMock()
        app.main(page)  # ヘッダーにボタンを足しても画面が組み立つこと
        control = rules.build(on_close=lambda: None, is_busy=lambda: False)
        assert control is not None

    def test_lists_a_user_rule(self, user_file):
        user_file.parent.mkdir(parents=True)
        user_file.write_text(DOCX_RULE, encoding="utf-8")
        conversion_registry.reload(user_file)
        ids = [rule_id for rule_id, _ in rules._grouped(conversion_registry.table())]
        assert "docx-to-pdfa" in ids and "image-to-tiff" in ids

    def test_missing_command_is_reported(self, user_file):
        """コマンドが見つからない規則は、画面で分かること（AIP では原本のまま残る）。"""
        user_file.parent.mkdir(parents=True)
        user_file.write_text(DOCX_RULE, encoding="utf-8")
        conversion_registry.reload(user_file)
        rule = conversion_registry.rule_for("fmt/412", DerivativePurpose.PRESERVATION)
        ok, message = rules._tool_status(rule)
        assert not ok and "ap-test-converter" in message


class TestReload:
    def test_takes_effect_without_restarting(self, user_file):
        """書いて「読み直す」を押せば、開き直さずに規則が効くこと。"""
        assert conversion_registry.rule_for("fmt/412", DerivativePurpose.PRESERVATION) is None
        user_file.parent.mkdir(parents=True)
        user_file.write_text(DOCX_RULE, encoding="utf-8")
        conversion_registry.reload()
        rule = conversion_registry.rule_for("fmt/412", DerivativePurpose.PRESERVATION)
        assert rule is not None and rule.rule_id == "docx-to-pdfa"

    def test_is_refused_while_busy(self, user_file):
        """処理の途中に読み直すと、1 つの AIP の中で違う規則が効いてしまう。"""
        user_file.parent.mkdir(parents=True)
        user_file.write_text(DOCX_RULE, encoding="utf-8")
        control = rules.build(on_close=lambda: None, is_busy=lambda: True)
        reload_button = _find_button(control, "読み直す")
        status = control.content.controls[4]
        status.update = body_update = MagicMock()
        control.content.controls[6].update = body_update
        reload_button.on_click(None)
        assert conversion_registry.rule_for("fmt/412", DerivativePurpose.PRESERVATION) is None
        assert "読み直せません" in status.value


def _find_button(control, label):
    row = control.content.controls[3]
    return next(b for b in row.controls if getattr(b, "content", None) == label
                or getattr(b, "text", None) == label)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows は画面から開いても PATH が全部届く")
class TestLocateOutsidePath:
    def test_finds_a_command_in_an_extra_folder(self, tmp_path, monkeypatch):
        """PATH が短くても、利用者がソフトを入れる定番の場所から見つけること。"""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "ap-test-converter"
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setattr(normalizer, "extra_tool_dirs", lambda: [bin_dir])
        assert normalizer.locate("ap-test-converter") == tool

    def test_path_still_comes_first(self, tmp_path, monkeypatch):
        """PATH で見つかるものの選ばれ方は変えない。"""
        first, second = tmp_path / "a", tmp_path / "b"
        for d in (first, second):
            d.mkdir()
            t = d / "ap-test-converter"
            t.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
            t.chmod(t.stat().st_mode | stat.S_IXUSR)
        monkeypatch.setenv("PATH", str(first))
        monkeypatch.setattr(normalizer, "extra_tool_dirs", lambda: [second])
        assert normalizer.locate("ap-test-converter") == first / "ap-test-converter"

    def test_includes_the_usual_install_folders(self):
        dirs = normalizer.extra_tool_dirs()
        assert Path.home() / ".local" / "bin" in dirs
        assert Path("/opt/homebrew/bin") in dirs
        assert os.name != "nt"


class TestDisabledCount:
    """ひな形の例（enabled: false）は読み込まないが、画面では件数を知らせる。"""

    def test_template_has_disabled_rules(self):
        assert rules.disabled_count(rules.TEMPLATE) >= 1

    def test_counts_only_disabled_fprules(self):
        text = (
            '[{"model": "fpr.fprule", "fields": {"uuid": "a", "enabled": false}},'
            ' {"model": "fpr.fprule", "fields": {"uuid": "b", "enabled": true}},'
            ' {"model": "fpr.fpcommand", "fields": {"uuid": "c", "enabled": false}}]'
        )
        assert rules.disabled_count(text) == 1

    def test_empty_or_broken_is_zero(self):
        assert rules.disabled_count("") == 0
        assert rules.disabled_count("{broken") == 0
        assert rules.disabled_count('{"model": "fpr.fprule"}') == 0
