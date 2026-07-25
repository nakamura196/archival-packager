"""個人情報（PII）スキャナ。

現行 Swift 実装の `Sources/SIP/PII.swift` に対応する。

テキストとして読めるファイルの中から、日本の現場で問題になりやすい個人情報候補を
正規表現＋検証（マイナンバーの検査用数字 / クレジットカードの Luhn）で検出する。
bulk_extractor を同梱する代わりの軽量・日本向け実装。

**出力は必ずマスクする。** レポート自体が PII の漏洩源になってはならない。

限界: プレーンテキスト相当（UTF-8 / Shift_JIS で復号できるもの）と PDF が対象。
docx/xlsx など圧縮アーカイブ内のテキストは深掘りしない（特性評価ツールの領分）。
"""

from __future__ import annotations

import re

from .models import PIIFinding

# 種別ラベル。CSV に出る文字列なので変更は出力互換に影響する。
KIND_MY_NUMBER = "マイナンバー"
KIND_CREDIT_CARD = "クレジットカード番号"
KIND_EMAIL = "メールアドレス"
KIND_PHONE = "電話番号"
KIND_POSTAL_CODE = "郵便番号"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# 郵便番号: 任意の〒 + 3桁-4桁。前後に数字/ハイフンが続く場合（電話番号の一部など）は除外。
_POSTAL_RE = re.compile(r"〒?\s?(?<![0-9\-])\d{3}-\d{4}(?![0-9\-])")
# 電話: 市外局番/携帯。区切りハイフンを要求して誤検出を抑える。
_PHONE_RE = re.compile(r"0\d{1,3}-\d{1,4}-\d{4}")
# 数字の塊（区切り込み 12〜19 桁相当）。桁数・検証は呼び出し側で行う。
_DIGIT_RUN_RE = re.compile(r"\d(?:[ \-]?\d){11,18}")


def scan(text: str) -> list[PIIFinding]:
    """テキストを行単位で走査して検出一覧を返す。"""
    out: list[PIIFinding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        out.extend(_scan_line(line, line_no))
    return out


def _scan_line(line: str, line_no: int) -> list[PIIFinding]:
    found: list[PIIFinding] = []

    for m in _EMAIL_RE.findall(line):
        found.append(PIIFinding(kind=KIND_EMAIL, masked=_mask_email(m), line=line_no))

    for m in _POSTAL_RE.findall(line):
        found.append(PIIFinding(kind=KIND_POSTAL_CODE, masked=_mask_tail(m, keep=0), line=line_no))

    for m in _PHONE_RE.findall(line):
        found.append(PIIFinding(kind=KIND_PHONE, masked=_mask_tail(m, keep=4), line=line_no))

    # 数字の塊は桁数と検証を通してから採用する。
    # 12 桁でマイナンバーの検査用数字が合う、または 13-19 桁で Luhn が通るものだけ。
    # 単に「12 桁の数字」を全部拾うと、日付や連番だらけの表で使い物にならなくなる。
    for token in _DIGIT_RUN_RE.findall(line):
        digits = "".join(ch for ch in token if ch.isdigit())
        if len(digits) == 12 and is_valid_my_number(digits):
            found.append(PIIFinding(kind=KIND_MY_NUMBER, masked=_mask_tail(digits, keep=4), line=line_no))
        elif 13 <= len(digits) <= 19 and luhn_valid(digits):
            found.append(
                PIIFinding(kind=KIND_CREDIT_CARD, masked=_mask_tail(digits, keep=4), line=line_no)
            )

    return found


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
