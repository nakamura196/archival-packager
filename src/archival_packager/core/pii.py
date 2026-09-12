"""個人情報（PII）スキャナ。

現行 Swift 実装の `Sources/SIP/PII.swift` に対応する。

テキストとして読めるファイルの中から、日本の現場で問題になりやすい個人情報候補を
正規表現＋検証（マイナンバーの検査用数字 / クレジットカードの Luhn）で検出する。
bulk_extractor を同梱する代わりの軽量・日本向け実装。

**出力は必ずマスクする。** レポート自体が PII の漏洩源になってはならない。

限界: プレーンテキスト相当（UTF-8 / Shift_JIS で復号できるもの）と PDF が対象。
docx/xlsx など圧縮アーカイブ内のテキストは深掘りしない（特性評価ツールの領分）。

精度の実測値と、この実装で原理的に検出できないものは `docs/pii-accuracy.md` に
まとめてある。検出件数 0 は「個人情報が無い」ことの証明にはならない。
"""

from __future__ import annotations

import re
import unicodedata

from .models import PIIFinding

# 種別ラベル。CSV に出る文字列なので変更は出力互換に影響する。
KIND_MY_NUMBER = "マイナンバー"
KIND_CREDIT_CARD = "クレジットカード番号"
KIND_EMAIL = "メールアドレス"
KIND_PHONE = "電話番号"
KIND_POSTAL_CODE = "郵便番号"

# 区切りに化けやすい記号を ASCII ハイフンに寄せる。
# 縦書き資料の OCR では区切りのハイフンが長音符（ー）や別種のダッシュになって出てくる。
# 正規化しないと「全角で書かれた申請書」「OCR した名簿」を丸ごと取りこぼす。
_DASHES = "‐‑‒–—―−ー"
_DASH_TABLE = {ord(ch): "-" for ch in _DASHES}

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 郵便番号。〒がある場合はハイフンを省略した 7 桁も拾う（〒が付いていれば別物の可能性が低い）。
# 〒が無い場合は 3-4 桁のハイフン付きだけ。前後に数字/ハイフンが続くもの（電話番号や
# 頁範囲の一部）は除外する。
# 〒付きの枝を先に書くのは、同じ数字を「〒付き」と「素」で二重に数えないため。
_POSTAL_RE = re.compile(
    r"〒\s*(\d{3}-?\d{4})(?![0-9\-])"
    r"|(?<![0-9\-A-Za-z.])(\d{3}-\d{4})(?![0-9\-])"
)
# 「第 003-0045 号」「頁 113-0033」のような文書番号・頁は郵便番号と同じ形をしている。
# 文書館の目録では郵便番号よりこちらの方が多いので、前後の語で振り落とす。
# 〒 が付いている場合はこの判定を通さない（〒 があれば別物の可能性は低い）。
_DOC_NUM_BEFORE = ("第", "頁", "ページ", "No.", "no.", "No", "#", "巻", "冊")
_DOC_NUM_AFTER = ("号", "番", "頁", "ページ", "巻", "冊")
# 電話。区切り（ハイフンか括弧）を要求して誤検出を抑える。
# 3 群目を 3 桁まで許すのはフリーダイヤル（0120-123-456）のため。
# 桁数の妥当性（合計 10 桁か 11 桁）は _PHONE_DIGIT_COUNTS で別途確認する。
_PHONE_RE = re.compile(
    r"(?<![0-9\-])(?:"
    r"0\d{1,3} ?- ?\d{1,4} ?- ?\d{3,4}"  # 03-1234-5678 / 0120-123-456 / 03 - 1234 - 5678
    r"|\(0\d{1,3}\)\s?\d{1,4}-?\d{3,4}"  # (03)1234-5678
    r"|0\d{1,3}\(\d{1,4}\)\d{3,4}"  # 03(1234)5678
    r")(?![0-9\-])"
)
_PHONE_DIGIT_COUNTS = frozenset({10, 11})
# 数字の塊（区切りはスペースかハイフン 1 個まで）。桁数・検証は呼び出し側で行う。
# 長さを正規表現側で絞らず貪欲に取り切るのは、**長い数字列の一部を切り出して
# 偶然 Luhn を通してしまう**のを避けるため（24 桁の整理番号の先頭 16 桁、など）。
_DIGIT_RUN_RE = re.compile(r"(?<![0-9])\d(?:[ \-]?\d)*")


def scan(text: str) -> list[PIIFinding]:
    """テキストを行単位で走査して検出一覧を返す。"""
    out: list[PIIFinding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        out.extend(_scan_line(_normalize(line), line_no))
    return out


def _normalize(line: str) -> str:
    """全角・互換文字を ASCII に寄せてから走査する。

    日本語の申請書は数字も＠も全角で書かれていることがあり、素の正規表現では
    一切引っかからない。NFKC で ０→0 / ＠→@ / ℡→TEL まで寄せ、そのうえで
    ダッシュ類をハイフンに揃える。行単位で正規化するので行番号はずれない。
    """
    return unicodedata.normalize("NFKC", line).translate(_DASH_TABLE)


def _scan_line(line: str, line_no: int) -> list[PIIFinding]:
    found: list[PIIFinding] = []

    for m in _EMAIL_RE.findall(line):
        found.append(PIIFinding(kind=KIND_EMAIL, masked=_mask_email(m), line=line_no))

    for m in _POSTAL_RE.finditer(line):
        marked = m.group(1) is not None
        if not marked and _looks_like_document_number(line, m.start(2), m.end(2)):
            continue
        value = m.group(1) or m.group(2)
        found.append(PIIFinding(kind=KIND_POSTAL_CODE, masked=_mask_tail(value, keep=0), line=line_no))

    for m in _PHONE_RE.findall(line):
        digits = "".join(ch for ch in m if ch.isdigit())
        if len(digits) in _PHONE_DIGIT_COUNTS:
            found.append(PIIFinding(kind=KIND_PHONE, masked=_mask_tail(m, keep=4), line=line_no))

    # 数字の塊は桁数と検証を通してから採用する。
    # 12 桁でマイナンバーの検査用数字が合う、または 13-19 桁で Luhn が通り、かつ
    # 実在するカードブランドの先頭桁を持つものだけ。
    # 単に「12 桁の数字」を全部拾うと、日付や連番だらけの表で使い物にならなくなる。
    for token in _DIGIT_RUN_RE.findall(line):
        digits = "".join(ch for ch in token if ch.isdigit())
        if len(digits) == 12 and is_valid_my_number(digits):
            found.append(PIIFinding(kind=KIND_MY_NUMBER, masked=_mask_tail(digits, keep=4), line=line_no))
        elif 13 <= len(digits) <= 19 and has_card_prefix(digits) and luhn_valid(digits):
            found.append(
                PIIFinding(kind=KIND_CREDIT_CARD, masked=_mask_tail(digits, keep=4), line=line_no)
            )

    return found


def _looks_like_document_number(line: str, start: int, end: int) -> bool:
    """郵便番号と同じ形の文書番号・頁番号かどうかを前後の語で判定する。"""
    before = line[:start].rstrip(" ")
    after = line[end:].lstrip(" ")
    return before.endswith(_DOC_NUM_BEFORE) or after.startswith(_DOC_NUM_AFTER)


def luhn_valid(digits: str) -> bool:
    """クレジットカード等の Luhn チェック。"""
    ds = [int(ch) for ch in digits if ch.isdigit()]
    if len(ds) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            doubled = d * 2
            total += doubled - 9 if doubled > 9 else doubled
        else:
            total += d
    return total % 10 == 0


def has_card_prefix(digits: str) -> bool:
    """実在するカードブランドの先頭桁かどうか。

    Luhn は 10 個に 1 個の割合で偶然通る。**先頭桁を見ないと ISBN-13（978/979 で
    始まる）や 13 桁の法人番号がそのままカード番号として報告される。**
    実在ブランドは 3（Amex/JCB/Diners）・4（Visa）・5（Mastercard）・6（Discover/
    UnionPay）か、Mastercard の 2 シリーズ（2221-2720）に限られるので、
    それ以外は落とす。見逃しを増やさずに誤検出だけを減らせる数少ない条件。
    """
    if not digits:
        return False
    if digits[0] in "3456":
        return True
    return len(digits) >= 4 and 2221 <= int(digits[:4]) <= 2720


def is_valid_my_number(digits: str) -> bool:
    """マイナンバー（個人番号）の検査用数字を検証する。

    検査用数字 = 11 - (Σ Pn×Qn mod 11)。ただし (Σ mod 11) <= 1 なら 0。
    Pn は検査用数字を除く下位 11 桁（最下位が n=1）。
    Qn = n+1 (1<=n<=6), n-5 (7<=n<=11)。
    """
    ds = [int(ch) for ch in digits if ch.isdigit()]
    if len(ds) != 12:
        return False

    check = ds[11]
    body = ds[:11]  # 左->右の 11 桁

    total = 0
    for n in range(1, 12):
        p = body[11 - n]  # n=1 が最下位（body の末尾）
        q = n + 1 if n <= 6 else n - 5
        total += p * q

    r = total % 11
    expected = 0 if r <= 1 else 11 - r
    return expected == check


def _mask_tail(s: str, *, keep: int) -> str:
    """末尾 keep 桁だけ残して他をマスクする（区切りは保持しない・数字列前提）。"""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) <= keep:
        return "*" * len(digits)
    return "*" * (len(digits) - keep) + digits[-keep:] if keep else "*" * len(digits)


def _mask_email(s: str) -> str:
    """ローカル部の先頭 1 文字だけ残す。ドメインは残す（どこ宛かは調査に必要）。"""
    at = s.find("@")
    if at < 0:
        return "***"
    local, domain = s[:at], s[at:]
    head = local[0] if local else ""
    return f"{head}***{domain}"
