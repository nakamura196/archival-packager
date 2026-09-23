"""core が作った文字を、**画面に出すときだけ**訳す。

## なぜ要るか

進捗の行、「目視確認が必要な点」、ファイル一覧の値（原本・保存用・未実施）は
core が日本語で作る。core は i18n を通さない（`i18n.py` の説明のとおり、
パッケージの中身は画面の言語に左右されてはならない）。そのため英語の画面でも、
いちばん長く眺める進捗の欄が日本語のまま出ていた。

海外のアーキビストを想定して画面を追ったところ、SIP 作成から AIP 作成まで、
進捗の欄は 1 行も読めなかった。

## 何を変え、何を変えないか

core が作る文字列そのもの（report.txt、CSV、PREMIS に書かれる値）は変えない。
ここは、画面に並べる直前に、決まった形の文を訳文に置き換えるだけ。
**知らない形の文はそのまま返す。** core に文が増えても、画面は日本語で
出るだけで壊れない（`t()` と同じ考え方）。

siegfried が返す英語の警告（"no match; possibilities based on extension are …"）
も、ここで平易な言葉にする。PRONOM の番号が数十個並んだ行は、技術者でない
担当者には何をすればよいのか分からない。原文はツールチップに残す。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from ..i18n import raw, t

_Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]


def _rule(pattern: str, render: Callable[[re.Match[str]], str]) -> _Rule:
    return re.compile(pattern), render


#: 進捗の行。core/sip_pipeline.py と core/aip_pipeline.py の progress() に対応する。
_PROGRESS: list[_Rule] = [
    _rule(raw(r"^入力フォルダを走査しています…$"),
          lambda m: t("入力フォルダを走査しています…")),
    _rule(raw(r"^Archivematica transfer 構造を検出しました（objects/ をそのまま尊重）$"),
          lambda m: t("Archivematica transfer 構造を検出しました（objects/ をそのまま尊重）")),
    _rule(raw(r"^入力 ZIP を展開しています…$"), lambda m: t("入力 ZIP を展開しています…")),
    _rule(raw(r"^対象ファイル: (\d+) 件$"),
          lambda m: t("対象ファイル: {count} 件", count=m[1])),
    _rule(raw(r"^ファイル名サニタイズ: (\d+) 件を安全な名前に変更（元名は accession.csv に保持）$"),
          lambda m: t("ファイル名を安全化: {count} 件の名前を変更しました"
                      "（元の名前は accession.csv に残ります）", count=m[1])),
    _rule(raw(r"^siegfried が同梱されていないため、フォーマット識別をスキップします。$"),
          lambda m: t("siegfried が同梱されていないため、フォーマット識別をスキップします。")),
    _rule(raw(r"^フォーマットを識別しています（siegfried）…$"),
          lambda m: t("フォーマットを識別しています（siegfried）…")),
    _rule(raw(r"^フォーマット識別に失敗したためスキップします: (.*)$"),
          lambda m: t("フォーマット識別に失敗したためスキップします: {detail}", detail=m[1])),
    _rule(raw(r"^画像の技術的特性を読み取っています…（(\d+) 件）$"),
          lambda m: t("画像の技術的特性を読み取っています…（{count} 件）", count=m[1])),
    _rule(raw(r"^チェックサム\(SHA-256\)を計算しています…$"),
          lambda m: t("チェックサム(SHA-256)を計算しています…")),
    _rule(raw(r"^ClamAV が同梱されていないため、ウイルスチェックをスキップします。$"),
          lambda m: t("ClamAV が同梱されていないため、ウイルスチェックをスキップします。")),
    _rule(raw(r"^ウイルス定義 DB が未取得のため、ウイルスチェックをスキップします。$"),
          lambda m: t("ウイルス定義 DB が未取得のため、ウイルスチェックをスキップします。")),
    _rule(raw(r"^ウイルスチェック中（ClamAV）…$"), lambda m: t("ウイルスチェック中（ClamAV）…")),
    _rule(raw(r"^ウイルスチェックに失敗しましたが、SIP 作成は続行します: (.*)$"),
          lambda m: t("ウイルスチェックに失敗しましたが、SIP 作成は続行します: {detail}",
                      detail=m[1])),
    _rule(raw(r"^ウイルスは検出されませんでした。$"),
          lambda m: t("ウイルスは検出されませんでした。")),
    _rule(raw(r"^ウイルス検出: (\d+) 件。report.txt を確認してください。$"),
          lambda m: t("ウイルス検出: {count} 件。report.txt を確認してください。", count=m[1])),
    _rule(raw(r"^個人情報\(PII\)をスキャンしています…$"),
          lambda m: t("個人情報(PII)をスキャンしています…")),
    _rule(raw(r"^PII 候補は見つかりませんでした。"
              r"(?: ただし (\d+) ファイルは中身を読めず、走査できていません。)?$"),
          lambda m: t("PII 候補は見つかりませんでした。") + (
              t(" ただし {count} ファイルは中身を読めず、走査できていません。", count=m[1])
              if m[1] else "")),
    _rule(raw(r"^PII 候補: (\d+) 件（(\d+) ファイル）。pii-report.csv を確認してください。"
              r"(?: ただし (\d+) ファイルは中身を読めず、走査できていません。)?$"),
          lambda m: t("PII 候補: {count} 件（{files} ファイル）。pii-report.csv を確認してください。",
                      count=m[1], files=m[2]) + (
              t(" ただし {count} ファイルは中身を読めず、走査できていません。", count=m[3])
              if m[3] else "")),
    _rule(raw(r"^スプレッドシートを生成しています…$"),
          lambda m: t("スプレッドシートを生成しています…")),
    _rule(raw(r"^入力の metadata/metadata.csv を継承します$"),
          lambda m: t("入力の metadata/metadata.csv を継承します")),
    _rule(raw(r"^前回の accession.csv を読めませんでした: (.*)。対応表をスキップします。$"),
          lambda m: t("前回の accession.csv を読めませんでした: {name}。対応表をスキップします。",
                      name=m[1])),
    _rule(raw(r"^配列前後を突合: (\d+)/(\d+) 行が一致（arrangement-map.csv）$"),
          lambda m: t("配列前後を突合: {matched}/{total} 行が一致（arrangement-map.csv）",
                      matched=m[1], total=m[2])),
    _rule(raw(r"^BagIt bag を組み立てています…$"), lambda m: t("BagIt bag を組み立てています…")),
    _rule(raw(r"^SIP を組み立てています…$"), lambda m: t("SIP を組み立てています…")),
    _rule(raw(r"^ZIP（無圧縮）に固めています…$"), lambda m: t("ZIP（無圧縮）に固めています…")),
    _rule(raw(r"^ZIP を作成しました: (.*)$"),
          lambda m: t("ZIP を作成しました: {name}", name=m[1])),
    _rule(raw(r"^完了しました。$"), lambda m: t("完了しました。")),
    # AIP
    _rule(raw(r"^入力を読み取っています（BagIt bag）…$"),
          lambda m: t("入力を読み取っています（BagIt bag）…")),
    _rule(raw(r"^入力を読み取っています（SIP ディレクトリ）…$"),
          lambda m: t("入力を読み取っています（SIP ディレクトリ）…")),
    _rule(raw(r"^原本 (\d+) 件（ハッシュ継承 (\d+) 件 / 再計算 (\d+) 件）$"),
          lambda m: t("原本 {count} 件（ハッシュ継承 {inherited} 件 / 再計算 {recomputed} 件）",
                      count=m[1], inherited=m[2], recomputed=m[3])),
    _rule(raw(r"^完全性を確認しています（マニフェスト照合）…$"),
          lambda m: t("完全性を確認しています（マニフェスト照合）…")),
    _rule(raw(r"^完全性確認: (\d+) 件すべて一致$"),
          lambda m: t("完全性確認: {count} 件すべて一致", count=m[1])),
    _rule(raw(r"^完全性確認: (\d+) 件の不一致。要確認$"),
          lambda m: t("完全性確認: {count} 件の不一致。要確認", count=m[1])),
    _rule(raw(r"^完全性確認をスキップ: (.*)$"),
          lambda m: t("完全性確認をスキップ: {reason}", reason=m[1])),
    _rule(raw(r"^フォーマット変換は行いません（オプション OFF）$"),
          lambda m: t("フォーマット変換は行いません（オプション OFF）")),
    _rule(raw(r"^正規化の対象はありません（既に保存に適した形式、または未知の形式）$"),
          lambda m: t("正規化の対象はありません（既に保存に適した形式、または未知の形式）")),
    _rule(raw(r"^フォーマット変換: (\d+) 件$"),
          lambda m: t("フォーマット変換: {count} 件", count=m[1])),
    _rule(raw(r"^METS を生成しています…$"), lambda m: t("METS を生成しています…")),
    _rule(raw(r"^AIP（BagIt bag）を組み立てています…$"),
          lambda m: t("AIP（BagIt bag）を組み立てています…")),
]

#: 「目視確認が必要な点」の 1 行は「種類: 対象」の形。種類だけを訳し、
#: 対象（ファイル名）はそのまま出す。core/sip_builder.py の collect_warnings ほか。
_WARNING_KINDS: dict[str, Callable[[], str]] = {
    raw("ウイルス検出"): lambda: t("ウイルス検出"),
    raw("PII候補"): lambda: t("個人情報の候補"),
    raw("未識別"): lambda: t("形式を特定できない"),
    raw("未識別フォーマット"): lambda: t("形式を特定できない"),
    raw("拡張子不一致"): lambda: t("拡張子と中身が食い違う"),
    raw("画像を読めません"): lambda: t("画像を読めません"),
    raw("パスが長すぎます（Windows で開けない可能性）"):
        lambda: t("パスが長すぎます（Windows で開けない可能性）"),
    raw("ファイル名が NFC 正規化されていません（Windows/Linux で別名と判定される可能性）"):
        lambda: t("ファイル名が NFC 正規化されていません（Windows/Linux で別名と判定される可能性）"),
    raw("変換ツールが無いため原本のまま保存"): lambda: t("変換ツールが無いため原本のまま保存"),
    raw("変換に失敗（原本のまま保存）"): lambda: t("変換に失敗（原本のまま保存）"),
    raw("変換結果を読み戻せないため原本のまま保存"):
        lambda: t("変換結果を読み戻せないため原本のまま保存"),
    raw("完全性確認をスキップ"): lambda: t("完全性確認をスキップ"),
    raw("変換規則表"): lambda: t("変換規則表"),
}

_MORE = re.compile(raw(r"^（他 (\d+) 件）$"))

#: 警告の種類ごとの、担当者が次にすること。種類の名前だけでは何をすれば
#: よいか分からない（「未識別 54 件」と出ても手が止まる）。
_WARNING_ADVICE: dict[str, Callable[[], str]] = {
    raw("ウイルス検出"): lambda: t(
        "ウイルス検出: 該当ファイルを開かず、担当の部署に相談してください。"),
    raw("PII候補"): lambda: t(
        "個人情報の候補: 公開の可否を判断するため、pii-report.csv で箇所を確かめてください。"),
    raw("未識別"): lambda: _unidentified_advice(),
    raw("未識別フォーマット"): lambda: _unidentified_advice(),
    raw("拡張子不一致"): lambda: t(
        "拡張子と中身が食い違う: 名前の末尾（.pdf など）と中身の形式が違います。"
        "開けるかどうかを確かめてください。"),
}


def _unidentified_advice() -> str:
    return t("形式を特定できない: 壊れていないか、ふだんのソフトで開けるかを確かめてください。"
             "開ければ、そのまま保存して差し支えありません。")


def progress_line(line: str) -> str:
    """進捗の 1 行を今の言語に。知らない形ならそのまま。"""
    for pattern, render in _PROGRESS:
        m = pattern.match(line)
        if m:
            return render(m)
    # 区切り線（「――― AIP 作成 ―――」）と SHA-256: n/m は画面側で作る／訳さなくても読める
    return line


def _split_warning(line: str) -> tuple[str, str]:
    kind, sep, rest = line.partition(": ")
    if sep and kind in _WARNING_KINDS:
        return kind, rest
    return "", line


def warning_line(line: str) -> str:
    """「目視確認が必要な点」の 1 行を今の言語に。"""
    kind, rest = _split_warning(line)
    more = _MORE.match(rest)
    if more:
        rest = t("（他 {count} 件）", count=more[1])
    if kind:
        return f"{_WARNING_KINDS[kind]()}: {rest}"
    more = _MORE.match(line)
    return t("（他 {count} 件）", count=more[1]) if more else line


def warning_advice(lines: list[str]) -> list[str]:
    """警告に出てきた種類について、次にすることを 1 行ずつ。出てきた順。"""
    seen: list[str] = []
    for line in lines:
        kind, _rest = _split_warning(line)
        if kind in _WARNING_ADVICE and _WARNING_ADVICE[kind] not in seen:
            seen.append(_WARNING_ADVICE[kind])
    return [make() for make in seen]


def format_warning(value: str) -> str:
    """siegfried の警告（英語の生の文）を、担当者が読める言葉に。

    番号の並んだ原文は、呼び出し側でツールチップに残す。
    """
    low = value.lower()
    if low.startswith("no match"):
        return t("形式を特定できませんでした")
    if "extension mismatch" in low:
        return t("拡張子と中身が食い違っています")
    if "match on extension only" in low:
        return t("拡張子だけで判定しました（中身では確かめていません）")
    if "match on filename only" in low:
        return t("ファイル名だけで判定しました（中身では確かめていません）")
    return value


def is_extension_mismatch(value: str) -> bool:
    """siegfried の警告が「拡張子と中身の食い違い」か。

    "no match"（形式を特定できない）や "match on extension only" を
    食い違いとして数えない。core/sip_builder.py の collect_warnings と同じ判定。
    """
    return "mismatch" in value.lower()


#: ファイル一覧の値。core が CSV に書く日本語。
_VALUES: dict[str, Callable[[], str]] = {
    raw("原本"): lambda: t("原本"),
    raw("保存用"): lambda: t("保存用"),
    raw("提出書類"): lambda: t("提出書類"),
    raw("未実施"): lambda: t("未実施"),
    raw("検出なし"): lambda: t("検出なし"),
}


def value(text: str) -> str:
    """ファイル一覧の区分・ウイルス検査の値を今の言語に。"""
    make = _VALUES.get(text)
    return make() if make else text


def pipeline_error(message: str) -> str:
    """処理を止めた理由（core の例外の文）を今の言語に。知らない形ならそのまま。"""
    if message.startswith(raw("出力先が資料のフォルダの中にあります。")):
        return t("出力先が資料のフォルダの中にあります。原本のフォルダに書き込まないよう、"
                 "別の場所を選んでください。")
    if message.startswith(raw("出力先が SIP のフォルダの中にあります。")):
        return t("出力先が SIP のフォルダの中にあります。SIP に書き込まないよう、"
                 "別の場所を選んでください。")
    if message == raw("入力フォルダに対象ファイルがありません。"):
        return t("入力フォルダに対象ファイルがありません。")
    if message.startswith(raw("SIP として認識できません: ")):
        return t("選んだフォルダは SIP ではありません（objects フォルダがありません）。")
    return message
