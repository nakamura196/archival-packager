"""画面の多言語対応（日本語 / 英語）。

ここで守りたいのは 3 つ。

  1. **訳し漏れで画面が壊れないこと。** 鍵は日本語の原文そのものなので、
     訳が無ければ日本語が出る。英語にならないだけで、空欄にはならない。
  2. **辞書とソースがずれたら気づけること。** 原文を直して辞書を直し忘れると
     訳が当たらなくなるが、画面は日本語で出てしまうため、動かしても気づけない。
     機械で突き合わせるほかない。
  3. **パッケージの中身が言語で変わらないこと。** ここが最も重い。
     情報パッケージは何十年も残る記録で、作ったときの画面の言語に
     中身が左右されてはならない。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from archival_packager import i18n
from archival_packager.locales import en

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "archival_packager" / "ui"
UI_FILES = sorted(UI_DIR.glob("*.py"))

#: 日本語とみなす文字。かな・漢字・全角記号・和文の約物を対象にする。
#: 三点リーダ（…）や罫線（―）は日本語に限らないので入れない。
JAPANESE = re.compile(r"[　-〿぀-ヿ㐀-䶿一-鿿＀-￯]")

#: 画面ではないので訳さない関数。自己診断は CI と開発者だけが読むもので、
#: 訳すと結果を拾っている側（署名後の macOS / Windows の確認）が読めなくなる。
NOT_SCREEN_TEXT = {"self_test"}


@pytest.fixture(autouse=True)
def isolated_language(tmp_path, monkeypatch):
    """言語の状態を、テストの外へ持ち出さない。

    i18n は**モジュールに現在の言語を持ち**、切り替えると設定ファイルにも書く。
    素で走らせると、このファイルが利用者本人の設定を書き換え、さらに
    あとから走るテスト（画面を組み立てて「実行」ボタンを探すもの）が
    英語の画面を見ることになる。設定の置き場を作業用に差し替え、
    終わったら元の言語へ戻す。
    """
    monkeypatch.setattr(i18n, "_settings_path", lambda: tmp_path / "settings.json")
    before = i18n.current_language()
    yield
    i18n.set_language(before)


# --------------------------------------------------------------------------
# ソースを読む道具
# --------------------------------------------------------------------------


def _docstring_ids(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            found.add(id(body[0].value))
    return found


def _excluded_ids(tree: ast.AST) -> set[int]:
    """訳の対象外と決めた関数の中にある文字列。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in NOT_SCREEN_TEXT:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant):
                    found.add(id(sub))
    return found


def _wrapped(tree: ast.AST) -> tuple[set[int], list[str], list[ast.Call]]:
    """t() / raw() に包まれた文字列。

    返すのは (その文字列ノードの id, t() の鍵, 第1引数が定数でない t() 呼び出し)。
    """
    ids: set[int] = set()
    keys: list[str] = []
    dynamic: list[ast.Call] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id not in ("t", "raw"):
            continue
        first = node.args[0] if node.args else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            ids.add(id(first))
            if node.func.id == "t":
                keys.append(first.value)
        elif node.func.id == "t":
            dynamic.append(node)
    return ids, keys, dynamic


def _source_keys() -> set[str]:
    keys: set[str] = set()
    for path in UI_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        keys.update(_wrapped(tree)[1])
    return keys


# --------------------------------------------------------------------------
# 1. 訳が無ければ原文
# --------------------------------------------------------------------------


class TestFallback:
    """**訳し漏れがあっても画面は壊れない。**

    鍵を `T_RUN` のような記号にすると、訳が無いときに画面へ記号が出る。
    原文を鍵にしておけば、出るのは日本語で済む。この性質が、
    訳を少しずつ足していける唯一の理由なので、ここで固定する。
    """

    def test_unknown_key_returns_the_japanese_source(self):
        i18n.set_language("en")
        assert i18n.t("まだ訳していない言葉") == "まだ訳していない言葉"

    def test_japanese_needs_no_dictionary(self):
        i18n.set_language("ja")
        assert i18n.t("実行") == "実行"

    def test_english_is_used_when_present(self):
        i18n.set_language("en")
        assert i18n.t("実行") == "Run"

    def test_values_are_substituted(self):
        """差し込みは訳文にも効くこと。訳したとたんに値が消えるのが最悪。"""
        i18n.set_language("en")
        assert i18n.t("目視確認が必要な点: {count} 件", count=3) == "Points to check by eye: 3"
        i18n.set_language("ja")
        assert i18n.t("目視確認が必要な点: {count} 件", count=3) == "目視確認が必要な点: 3 件"


# --------------------------------------------------------------------------
# 2. 辞書とソースがずれていないこと
# --------------------------------------------------------------------------


class TestDictionaryMatchesTheScreen:
    """辞書に、画面のどこにも無い鍵が残っていないこと。

    原文を直すと鍵が変わる。辞書側を直し忘れても画面は日本語で出るだけなので、
    **動かしても気づけない**。残骸を見つけるにはソースと突き合わせるしかない。
    逆向き（ソースにあって辞書に無い＝未訳）は不足であって誤りではないので、
    ここでは落とさず、数だけ知らせる。
    """

    def test_every_english_key_exists_in_the_ui_source(self):
        stale = sorted(set(en.TEXTS) - _source_keys())
        assert not stale, (
            "画面のどこにも無い鍵が locales/en.py に残っています"
            "（原文を直して辞書を直し忘れた可能性）:\n  " + "\n  ".join(stale)
        )

    def test_translation_keys_are_written_as_literals(self):
        """t() の第1引数は必ず文字列リテラルであること。

        `t(variable)` と書かれると、上の突き合わせが何も見えなくなる
        （辞書の残骸も未訳も検出できない）。抽出できる形に限る。
        """
        dynamic = []
        for path in UI_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            dynamic += [f"{path.name}:{n.lineno}" for n in _wrapped(tree)[2]]
        assert not dynamic, "t() に変数を渡しています: " + ", ".join(dynamic)

    def test_untranslated_strings_are_reported(self):
        """未訳は落とさない。ただし黙って増えていくのは避けたいので数を出す。"""
        missing = sorted(_source_keys() - set(en.TEXTS))
        print(f"未訳: {len(missing)} 件 / 全 {len(_source_keys())} 件")


# --------------------------------------------------------------------------
# 3. 訳し忘れが残っていないこと
# --------------------------------------------------------------------------


def test_no_bare_japanese_literals_in_the_ui():
    """画面のソースに、t() で包んでいない日本語が残っていないこと。

    包み忘れたところだけが日本語のまま出る、という半端な画面を防ぐ。
    見て回って気づくのは難しい（英語にしたときにしか現れず、しかも
    エラーにはならない）ので、機械で見つける。

    除くのは docstring とコメント、自己診断（画面ではない）、
    そして raw() で「訳さないと決めた」と印を付けたもの。
    """
    offenders: list[str] = []
    for path in UI_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        allowed = _wrapped(tree)[0] | _docstring_ids(tree) | _excluded_ids(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in allowed or not JAPANESE.search(node.value):
                continue
            offenders.append(f"{path.name}:{node.lineno}: {node.value[:40]!r}")
    assert not offenders, (
        "t() で包んでいない日本語があります（訳さないと決めたものは raw() で包む）:\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# 4. 壊れた設定でも起動できること
# --------------------------------------------------------------------------


class TestBrokenSettingsDoNotStopTheApp:
    """設定が壊れていても起動すること。

    起動できなければ設定を直す手段も無くなる。利用者が手で書き換えた、
    将来の版で消えた言語コードが残っている、書きかけで落ちた、
    いずれでもアプリは開かなければならない。
    """

    def test_unknown_language_falls_back_to_japanese(self):
        i18n.set_language("kr")
        assert i18n.current_language() == "ja"
        assert i18n.t("実行") == "実行"

    def test_garbage_in_the_settings_file_is_ignored(self, tmp_path):
        (tmp_path / "settings.json").write_text("{ これは JSON ではない", encoding="utf-8")
        assert i18n._load_saved() == "ja"

    def test_unwritable_settings_do_not_raise(self, monkeypatch, tmp_path):
        """保存できないことは、その回の切り替えを妨げない。"""
        monkeypatch.setattr(i18n, "_settings_path", lambda: tmp_path / "無い階層" / "s.json")
        monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError()))
        i18n.set_language("en")
        assert i18n.current_language() == "en"

    def test_the_choice_is_remembered(self, tmp_path):
        i18n.set_language("en")
        assert i18n._load_saved() == "en"
        assert "language" in (tmp_path / "settings.json").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 5. パッケージの中身は言語で変わらない
# --------------------------------------------------------------------------


class TestPackageContentsAreNotTranslated:
    """**画面の言語を変えても、作られるパッケージの中身は変わらない。**

    2 つの理由がある。

      1. `core/sip_reader.py` は日本語の見出しで CSV を読み戻す。見出しが
         言語で変わると、英語で作った SIP を日本語のアプリが読めなくなる
         （その逆も同じ）。移管元と移管先で設定が違うだけで壊れてしまう。
      2. パッケージの中身は保存の記録そのものである。何十年も残る記録が、
         作った担当者の画面設定で書き分けられていてはならない。
    """

    def test_csv_headings_stay_japanese_in_english_mode(self):
        from archival_packager.core import spreadsheets

        i18n.set_language("en")

        # 画面では確かに訳される言葉であること（訳が無いから変わらない、では
        # この検査に意味が無い）。
        assert i18n.t("相対パス") == "Relative path"

        assert spreadsheets.formats([]).startswith("相対パス,")
        assert spreadsheets.accession([]).startswith("原パス（受入時）,")
        assert "ウイルス検査" in spreadsheets.formats([])

    def test_virus_state_labels_stay_japanese(self):
        """「未実施」と「検出なし」は AIP 作成時に読み戻して PREMIS に書く。"""
        from archival_packager.core import spreadsheets

        i18n.set_language("en")
        assert spreadsheets.VIRUS_NOT_SCANNED == "未実施"
        assert spreadsheets.VIRUS_CLEAN == "検出なし"

    def test_core_does_not_import_the_translator(self):
        """core から i18n を呼べば、いつか中身が訳される。入口を塞ぐ。"""
        core = Path(__file__).resolve().parent.parent / "src" / "archival_packager" / "core"
        offenders = [
            p.name
            for p in sorted(core.glob("*.py"))
            if "i18n" in p.read_text(encoding="utf-8")
        ]
        assert not offenders, f"core が i18n を参照しています: {offenders}"


class TestInitialLanguage:
    """初回起動で何語の画面を出すか。

    ストアに英語で掲載する以上、英語の環境で日本語の画面から始まると、
    掲載情報と実物が食い違う。いっぽう、一度選んだものを OS の言語で
    上書きすると「日本語の環境で英語の画面を使う」ができなくなる。
    その 2 つの境目をここで押さえる。
    """

    def test_reads_the_system_language(self, monkeypatch):
        import locale as locale_module

        monkeypatch.setattr(i18n.sys, "platform", "darwin")
        monkeypatch.setattr(locale_module, "getlocale", lambda *a: ("en_US", "UTF-8"))
        assert i18n.system_language() == "en"

        monkeypatch.setattr(locale_module, "getlocale", lambda *a: ("ja_JP", "UTF-8"))
        assert i18n.system_language() == "ja"

    def test_unknown_language_falls_back_to_english(self, monkeypatch):
        """訳を持たない言語では英語を出す。日本語ではない。

        ドイツ語や韓国語の環境で日本語の画面を出しても読めない。
        原文が日本語なのは実装の都合であって、利用者の既定ではない。
        """
        import locale as locale_module

        monkeypatch.setattr(i18n.sys, "platform", "darwin")
        for tag in ("ko_KR", "de_DE", "fr_FR", "zh_CN"):
            monkeypatch.setattr(locale_module, "getlocale", lambda *a, t=tag: (t, "UTF-8"))
            assert i18n.system_language() == "en", tag

    def test_detection_failure_does_not_raise(self, monkeypatch):
        """言語が分からないことは、起動を止める理由にならない。

        分からないときも英語にする。「日本語だと分かった」ときだけ日本語。
        """
        import locale as locale_module

        def boom(*a, **kw):
            raise RuntimeError("ロケールを読めません")

        monkeypatch.setattr(i18n.sys, "platform", "darwin")
        monkeypatch.setattr(locale_module, "getlocale", boom)
        monkeypatch.setattr(i18n.os, "environ", {})
        assert i18n.system_language() == "en"

    def test_english_fallback_needs_a_complete_dictionary(self):
        """英語に落とす前提として、訳が全部埋まっていること。

        欠けていれば、その行だけ日本語で出る。日本語を読まない利用者に
        混ざった画面を見せることになるので、fallback の言語は
        **訳し漏れが無いこと**とセットでなければならない。
        """
        from archival_packager.locales import en as en_table

        missing = sorted(_source_keys() - set(en_table.TEXTS))
        assert not missing, (
            f"{i18n.INITIAL_FALLBACK} に落とすのに未訳が {len(missing)} 件: {missing[:5]}"
        )

    def test_saved_choice_wins_over_the_system(self, monkeypatch, tmp_path):
        """一度選んだ言語は、OS の言語より優先する。"""
        import json

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"language": "en"}), encoding="utf-8")
        monkeypatch.setattr(i18n, "_settings_path", lambda: settings)
        monkeypatch.setattr(i18n, "system_language", lambda: "ja")
        assert i18n._load_saved() == "en"

    def test_no_settings_file_uses_the_system(self, monkeypatch, tmp_path):
        monkeypatch.setattr(i18n, "_settings_path", lambda: tmp_path / "none.json")
        monkeypatch.setattr(i18n, "system_language", lambda: "en")
        assert i18n._load_saved() == "en"

