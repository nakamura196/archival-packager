"""画面の言語（日本語 / 英語）。

## 翻訳の鍵は日本語の原文そのものにする

gettext の msgid と同じ考え方で、`t("実行")` のように**原文を鍵**にする。
訳が無ければ原文がそのまま返るので、**訳し漏れがあっても画面は壊れない**。
訳語の一覧（locales/en.py）が古くなっても、そこだけ日本語に戻るだけで済む。
`T_RUN_BUTTON` のような記号を鍵にすると、鍵を見ても何が出るのか分からず、
訳が無いときに画面へ記号がそのまま出る。

## 訳すのは画面だけ。パッケージの中身は訳さない

`core/` が作る文字列（report.txt の本文、CSV の見出し、PREMIS の記録、
進捗の行）はここを通さない。**パッケージの中身は保存の記録そのもの**であり、
`core/sip_reader.py` は日本語の見出しで CSV を読み戻す。言語によって見出しが
変わると、英語で作った SIP を日本語のアプリ（およびその逆）が読めなくなる。
情報パッケージは何十年も残る前提のもので、作った時の画面の言語に
中身が左右されてはならない。

## 言語の保存

`set_language()` は設定ファイルにも書く。切り替えと保存が別々の手続きだと、
片方を呼び忘れて「切り替えたのに次の起動で戻る」が起きる。
読み書きはいずれも失敗しても例外を外に出さない。**設定が壊れていても
アプリは起動できなければならない**（起動できなければ設定を直す手段も無い）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import applog
from .locales import en

#: 選べる言語。値は「その言語の話者が見て分かる表記」にする
#: （英語話者に「日本語」と出しても選べない、の逆も同じ）。
AVAILABLE: dict[str, str] = {"ja": "日本語", "en": "English"}

#: 既定の言語。原文がこれなので、辞書を引かずに済む。
DEFAULT = "ja"

#: 言語コード -> 訳語の対応表。"ja" は原文なので表を持たない。
_TABLES: dict[str, dict[str, str]] = {"en": en.TEXTS}

_SETTINGS_NAME = "settings.json"


def _settings_path() -> Path:
    """設定ファイルの場所。

    記録（errors.log）と同じ場所に置く。OS ごとの置き場の判断を applog に
    一本化でき、しかも「そこに書ける」ことが既に分かっている場所である。
    """
    return applog.log_path().parent / _SETTINGS_NAME


def _load_saved() -> str:
    """保存された言語を読む。読めなければ既定。

    設定ファイルが壊れていても、無くても、権限が無くても起動は妨げない。
    """
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT
    if not isinstance(data, dict):
        return DEFAULT
    lang = data.get("language")
    return lang if isinstance(lang, str) and lang in AVAILABLE else DEFAULT


def _save(lang: str) -> None:
    """言語を書き残す。書けなくても呼び出し元は止めない。

    保存できないこと自体は作業を妨げない（その回は切り替わっている）ので、
    例外を投げ返さない。他の設定が増えたときに消してしまわないよう、
    既にある内容へ上書きする形で書く。
    """
    path = _settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["language"] = lang
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return


_current: str = _load_saved()


def current_language() -> str:
    return _current


def set_language(lang: str) -> None:
    """言語を切り替え、設定にも残す。

    知らない言語コードが来たら既定に落とす。**例外は投げない。**
    設定ファイルを手で編集されたり、将来の版で消えた言語コードが
    残っていたりしても、起動できなくなってはならない。
    """
    global _current
    _current = lang if lang in AVAILABLE else DEFAULT
    _save(_current)


def t(text: str, **kwargs: object) -> str:
    """原文を今の言語に訳す。差し込む値があれば `str.format` を適用する。

    訳が無ければ原文をそのまま返す。訳文の差し込み欄（`{...}`）が原文と
    食い違っていても、原文で組み立て直して画面には何かが出るようにする。
    訳の間違いで画面が落ちるほうが、訳が出ないことより悪い。
    """
    translated = _TABLES.get(_current, {}).get(text, text)
    if not kwargs:
        return translated
    for candidate in (translated, text):
        try:
            return candidate.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            continue
    return text


def raw(text: str) -> str:
    """**訳さないと決めた**日本語。そのまま返す。

    core が作った値と突き合わせるための文字が対象で、訳すと突合が外れる。
    `t()` で包んでいない日本語を探すテスト（tests/test_i18n.py）に対して、
    「訳し忘れではなく、そう決めた」と示す印でもある。
    """
    return text
