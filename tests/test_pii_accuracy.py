"""PII 検出の精度を測り、下がったら気づけるように数字で固定するテスト。

**なぜこのテストがあるか。**
このアプリは「個人情報の候補の検出」を機能として掲げているが、実装は正規表現と
検査用数字だけの軽い仕組みで、どれだけ見逃すか・どれだけ誤検出するかを
一度も測らないまま公開されていた。文書館の担当者が「検出 0 件」を見て
「個人情報は無かった」と判断する場面を想定すると、**測っていない検出率を
根拠に使わせるのが一番危険**なので、正解ラベル付きのデータで実測し、
その値をここで固定する。

しきい値は実測値より少し緩めてある。**上げるためではなく、下がったときに
落とすためのもの**なので、実装を変えて数字が下がったらまず実装を疑う。
実測値と限界は docs/pii-accuracy.md にまとめてある。数字を動かしたら
あちらも直すこと。

評価データ（tests/fixtures/pii/cases.jsonl）はすべて合成データで、
実在の個人情報は含まない。カード番号は各社が公開している試験用番号、
マイナンバーは検査用数字を算出して作ったもの。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pytest

from archival_packager.core import pii

CASES_PATH = Path(__file__).parent / "fixtures" / "pii" / "cases.jsonl"

KINDS = (
    pii.KIND_MY_NUMBER,
    pii.KIND_CREDIT_CARD,
    pii.KIND_EMAIL,
    pii.KIND_PHONE,
    pii.KIND_POSTAL_CODE,
)

# 種別ごとのしきい値（適合率, 再現率）。2026-09-12 の実測値を少し下回る位置に置く。
# 実測: マイナンバー 0.8667/0.9286、カード 0.8000/0.8571、メール 1.0/1.0、
#       電話 1.0/0.8571、郵便 1.0/1.0、全体 0.9275/0.9143
THRESHOLDS: dict[str, tuple[float, float]] = {
    pii.KIND_MY_NUMBER: (0.80, 0.85),
    pii.KIND_CREDIT_CARD: (0.72, 0.80),
    pii.KIND_EMAIL: (0.95, 0.95),
    pii.KIND_PHONE: (0.92, 0.80),
    pii.KIND_POSTAL_CODE: (0.92, 0.92),
}
OVERALL_THRESHOLD = (0.88, 0.86)


@dataclass(frozen=True)
class Case:
    id: str
    text: str
    expect: tuple[str, ...]
    category: str
    note: str
    deliberate_miss: bool


def load_cases() -> list[Case]:
    out: list[Case] = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        out.append(
            Case(
                id=d["id"],
                text=d["text"],
                expect=tuple(d["expect"]),
                category=d["category"],
                note=d["note"],
                deliberate_miss=bool(d.get("deliberate_miss", False)),
            )
        )
    return out


CASES = load_cases()


class Tally:
    """種別ごとの TP/FP/FN を数える。

    1 行に同じ種別が 2 件あるケース（「甲 …／乙 …」のような並記）を
    正しく評価したいので、集合ではなく多重集合として突き合わせる。
    """

    def __init__(self) -> None:
        self.tp: Counter[str] = Counter()
        self.fp: Counter[str] = Counter()
        self.fn: Counter[str] = Counter()

    def add(self, expected: Counter[str], got: Counter[str]) -> None:
        for kind in set(expected) | set(got):
            hit = min(expected[kind], got[kind])
            self.tp[kind] += hit
            self.fp[kind] += got[kind] - hit
            self.fn[kind] += expected[kind] - hit

    def precision(self, kind: str | None = None) -> float:
        tp, fp = self._sum(self.tp, kind), self._sum(self.fp, kind)
        return tp / (tp + fp) if tp + fp else 1.0

    def recall(self, kind: str | None = None) -> float:
        tp, fn = self._sum(self.tp, kind), self._sum(self.fn, kind)
        return tp / (tp + fn) if tp + fn else 1.0

    def f1(self, kind: str | None = None) -> float:
        p, r = self.precision(kind), self.recall(kind)
        return 2 * p * r / (p + r) if p + r else 0.0

    @staticmethod
    def _sum(counter: Counter[str], kind: str | None) -> int:
        return counter[kind] if kind else sum(counter.values())


def tally(cases: list[Case]) -> Tally:
    t = Tally()
    for case in cases:
        t.add(Counter(case.expect), Counter(f.kind for f in pii.scan(case.text)))
    return t


@pytest.fixture(scope="module")
def overall() -> Tally:
    return tally(CASES)


class TestFixtureItself:
    """評価データ自体が壊れていないことを先に確かめる。

    正解ラベルが間違っていると、精度の数字が静かに嘘になる。
    特に「難しいケースを消して数字を上げる」という直し方を防ぎたい。
    """

    def test_ids_are_unique(self):
        ids = [c.id for c in CASES]
        assert len(ids) == len(set(ids))

    def test_case_count_does_not_shrink(self):
        """件数が減る変更は、たいてい難しいケースの削除。"""
        assert len(CASES) >= 119

    @pytest.mark.parametrize(
        ("category", "minimum"),
        [("positive", 55), ("negative", 40), ("adversarial", 5), ("unsupported", 10)],
    )
    def test_each_category_is_represented(self, category, minimum):
        assert sum(1 for c in CASES if c.category == category) >= minimum

    @pytest.mark.parametrize("kind", KINDS)
    def test_every_kind_has_positive_cases(self, kind):
        assert sum(1 for c in CASES if kind in c.expect) >= 5

    def test_labelled_my_numbers_really_have_valid_check_digits(self):
        """陽性ラベルの番号が実は不正だと、再現率が測れているように見えて測れていない。"""
        for case in CASES:
            if pii.KIND_MY_NUMBER not in case.expect:
                continue
            digits = [d for d in _digit_runs(case.text) if len(d) == 12]
            assert digits, case.id
            assert any(pii.is_valid_my_number(d) for d in digits), case.id

    def test_labelled_cards_really_pass_luhn(self):
        for case in CASES:
            if pii.KIND_CREDIT_CARD not in case.expect:
                continue
            if case.deliberate_miss:
                continue  # 桁が分断されているケースは番号として取り出せない
            digits = [d for d in _digit_runs(case.text) if 13 <= len(d) <= 19]
            assert any(pii.luhn_valid(d) for d in digits), case.id

    def test_expected_kinds_are_known(self):
        for case in CASES:
            for kind in case.expect:
                assert kind in KINDS, case.id


class TestAccuracy:
    """種別ごとの適合率・再現率・F値。下がったら落とすための固定。"""

    @pytest.mark.parametrize("kind", KINDS)
    def test_precision_and_recall(self, overall, kind):
        min_p, min_r = THRESHOLDS[kind]
        assert overall.precision(kind) >= min_p, f"{kind} の適合率が下がった"
        assert overall.recall(kind) >= min_r, f"{kind} の再現率が下がった"

    def test_overall(self, overall):
        min_p, min_r = OVERALL_THRESHOLD
        assert overall.precision() >= min_p
        assert overall.recall() >= min_r

    def test_recall_within_supported_notation(self):
        """漢数字・表で分断された番号など、取れないと分かっている表記を除いた再現率。

        ここが 1.0 を割ったら、対象と決めた範囲の中で見逃しが出たということ。
        全体の再現率（上のテスト）より甘い数字なので、**外向きに出す数字ではない**。
        """
        t = tally([c for c in CASES if not c.deliberate_miss])
        assert t.recall() >= 0.98

    def test_clean_negatives_do_not_fire(self, overall):
        """検証を偶然通るケース（adversarial）を除いた陰性では誤検出ゼロを保つ。

        文書館の目録・台帳にありがちな番号で毎回警告が出ると、
        警告そのものが無視されるようになり、本物の検出まで読み飛ばされる。
        """
        noisy = [
            (c.id, [f.kind for f in pii.scan(c.text)])
            for c in CASES
            if c.category == "negative" and pii.scan(c.text)
        ]
        assert noisy == []


class TestKnownGaps:
    """検出できないと分かっているものを、分かった形で残すためのテスト。

    ここが落ちたら「直った」ということなので、テストを消すのではなく
    docs/pii-accuracy.md の限界の記述を更新する。
    """

    @pytest.mark.parametrize("case", [c for c in CASES if c.category == "unsupported"], ids=lambda c: c.id)
    def test_unsupported_kinds_are_silently_missed(self, case):
        """住所・氏名・生年月日・口座は検出対象ですらない。

        ストアの説明を読んだ利用者は「個人情報を検出してくれる」と読む。
        実際に見ているのは 5 種別だけ、という差をここに残す。
        """
        assert pii.scan(case.text) == [], case.note

    @pytest.mark.parametrize(
        "case", [c for c in CASES if c.deliberate_miss], ids=lambda c: c.id
    )
    def test_deliberate_misses_stay_missed(self, case):
        """区切りの無い番号・漢数字・表で分断された番号は取れない。"""
        got = [f.kind for f in pii.scan(case.text)]
        assert not set(case.expect) & set(got), case.note

    @pytest.mark.parametrize(
        "case", [c for c in CASES if c.category == "adversarial"], ids=lambda c: c.id
    )
    def test_check_digit_collisions_are_reported(self, case):
        """検査用数字や Luhn を偶然通る別番号は、原理的に選り分けられない。

        12 桁の任意の数字はおよそ 11 回に 1 回マイナンバーの検査用数字を満たし、
        13 桁の数値コードはおよそ 10 回に 1 回 Luhn を満たす。
        「候補」であって「個人情報」ではない、という表示が要る理由。
        """
        assert pii.scan(case.text) != [], case.note


class TestMaskingUnderLoad:
    def test_no_finding_ever_leaks_the_source_digits(self):
        """レポートが漏洩源にならないこと。評価データ全件で確かめる。"""
        for case in CASES:
            for finding in pii.scan(case.text):
                body = finding.masked.split("@")[0]
                assert "*" in finding.masked, case.id
                assert len(body.replace("*", "")) <= 4, f"{case.id}: 残す桁が多すぎる"


def _digit_runs(text: str) -> list[str]:
    """テキスト中の数字の塊（区切りを除いた数字列）を返す。評価データ検証用。"""
    import re
    import unicodedata

    normalized = unicodedata.normalize("NFKC", text).translate({ord(c): "-" for c in "‐‑‒–—―−ー"})
    return ["".join(ch for ch in m if ch.isdigit()) for m in re.findall(r"\d(?:[ \-.]?\d)*", normalized)]


def _print_report() -> None:
    """docs/pii-accuracy.md を更新するときに手で叩くための表示。"""
    t = tally(CASES)
    print(f"{'種別':<16}{'TP':>4}{'FP':>4}{'FN':>4}   適合率   再現率    F値")
    for kind in KINDS:
        print(
            f"{kind:<16}{t.tp[kind]:>4}{t.fp[kind]:>4}{t.fn[kind]:>4}"
            f"   {t.precision(kind):.4f}  {t.recall(kind):.4f}  {t.f1(kind):.4f}"
        )
    print(
        f"{'合計':<16}{sum(t.tp.values()):>4}{sum(t.fp.values()):>4}{sum(t.fn.values()):>4}"
        f"   {t.precision():.4f}  {t.recall():.4f}  {t.f1():.4f}"
    )


if __name__ == "__main__":
    _print_report()
