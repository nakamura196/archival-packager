"""PII 走査・テキスト抽出・DFXML のテスト。

PII は誤検出（偽陽性）と見落とし（偽陰性）の両方が問題になる。
偽陽性が多いと「毎回警告が出るもの」として無視されるようになり、
結果的に本物の検出も見落とされる。両方向を固定する。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import dfxml, document_text, pii
from archival_packager.core.models import ScannedFile


def sf(rel: str, **kw) -> ScannedFile:
    return ScannedFile(
        relative_path=rel,
        absolute_path=kw.pop("absolute_path", Path("/x") / rel),
        size_bytes=kw.pop("size_bytes", 10),
        modified=kw.pop("modified", datetime(2026, 7, 25, tzinfo=UTC)),
        **kw,
    )


class TestMyNumberCheckDigit:
    # 検査用数字が正しい 12 桁を、アルゴリズムから逆算して用意する。
    def _with_valid_check_digit(self, body11: str) -> str:
        ds = [int(c) for c in body11]
        total = sum(ds[11 - n] * (n + 1 if n <= 6 else n - 5) for n in range(1, 12))
        r = total % 11
        check = 0 if r <= 1 else 11 - r
        return body11 + str(check)

    def test_accepts_valid_numbers(self):
        for body in ("12345678901", "00000000000", "98765432109"):
            n = self._with_valid_check_digit(body)
            assert pii.is_valid_my_number(n), n

    def test_rejects_wrong_check_digit(self):
        n = self._with_valid_check_digit("12345678901")
        wrong = n[:11] + str((int(n[11]) + 1) % 10)
        assert not pii.is_valid_my_number(wrong)

    @pytest.mark.parametrize("bad", ["", "1", "1234567890", "1234567890123"])
    def test_rejects_wrong_length(self, bad):
        assert not pii.is_valid_my_number(bad)


class TestLuhn:
    @pytest.mark.parametrize("number", ["4242424242424242", "4111111111111111", "5555555555554444"])
    def test_accepts_known_test_numbers(self, number):
        assert pii.luhn_valid(number)

    def test_rejects_altered_digit(self):
        assert not pii.luhn_valid("4242424242424243")

    def test_rejects_too_short(self):
        assert not pii.luhn_valid("424242424242")  # 12 桁


class TestDetection:
    def test_email(self):
        found = pii.scan("連絡先は taro.yamada@example.co.jp です")
        assert [f.kind for f in found] == [pii.KIND_EMAIL]
        assert found[0].masked == "t***@example.co.jp"
        assert found[0].line == 1

    def test_phone(self):
        found = pii.scan("電話 03-1234-5678")
        assert found[0].kind == pii.KIND_PHONE
        # 数字は 10 桁（0312345678）。末尾 4 桁を残すので "*" は 6 個。
        assert found[0].masked == "******5678"

    def test_mask_keeps_no_digits_when_keep_is_zero(self):
        """keep=0 のとき負のスライスで全桁が残ってしまう実装ミスをしやすい箇所。"""
        assert pii._mask_tail("113-0033", keep=0) == "*******"

    def test_postal_code(self):
        found = pii.scan("〒113-0033 東京都")
        assert found[0].kind == pii.KIND_POSTAL_CODE
        assert found[0].masked == "*******", "郵便番号は全桁マスク"

    def test_credit_card(self):
        found = pii.scan("カード 4242-4242-4242-4242")
        kinds = [f.kind for f in found]
        assert pii.KIND_CREDIT_CARD in kinds
        card = next(f for f in found if f.kind == pii.KIND_CREDIT_CARD)
        assert card.masked.endswith("4242")
        assert card.masked.count("*") == 12

    def test_line_numbers(self):
        found = pii.scan("1 行目\na@example.com\n3 行目\nb@example.com")
        assert [f.line for f in found] == [2, 4]

    def test_raw_value_is_never_reported(self):
        """レポートが漏洩源にならないこと。"""
        found = pii.scan("taro@example.com 03-1234-5678")
        blob = " ".join(f.masked for f in found)
        assert "taro" not in blob
        assert "1234-5678" not in blob


class TestFalsePositives:
    """偽陽性が多いと警告そのものが無視されるようになる。"""

    def test_plain_12_digit_number_is_not_flagged(self):
        """検査用数字が合わない 12 桁は拾わない（連番や日付の羅列）。"""
        found = pii.scan("整理番号 111111111111")
        assert [f for f in found if f.kind == pii.KIND_MY_NUMBER] == []

    def test_13_digit_number_failing_luhn_is_not_flagged(self):
        found = pii.scan("識別子 1234567890123")
        assert [f for f in found if f.kind == pii.KIND_CREDIT_CARD] == []

    def test_phone_without_separators_is_not_flagged(self):
        """区切りの無い数字列は誤検出が多いので拾わない（Swift 版と同じ方針）。"""
        assert [f for f in pii.scan("0312345678") if f.kind == pii.KIND_PHONE] == []

    def test_postal_like_inside_longer_number_is_not_flagged(self):
        assert [f for f in pii.scan("1234-56789") if f.kind == pii.KIND_POSTAL_CODE] == []

    def test_clean_text_yields_nothing(self):
        assert pii.scan("これは普通の文章です。数字は 42 個。") == []


class TestNormalizationAndGuards:
    """精度測定（test_pii_accuracy.py）で見つかった穴をふさいだ分の単体テスト。

    表記のゆれで丸ごと取りこぼす／目録の番号で毎回警告が出る、という
    2 つの実害に直結する箇所なので、しきい値とは別に個別に固定する。
    """

    def test_fullwidth_digits_and_at_sign(self):
        """日本語の申請書は数字も＠も全角で書かれていることがある。"""
        found = pii.scan("電話 ０３－１２３４－５６７８　ｙａｍａｄａ＠ｅｘａｍｐｌｅ．ｊｐ")
        assert {f.kind for f in found} == {pii.KIND_PHONE, pii.KIND_EMAIL}

    def test_ocr_dash_variants(self):
        """縦書き資料の OCR では区切りが長音符や別種のダッシュになって出てくる。"""
        assert [f.kind for f in pii.scan("自宅 06ー6543ー2109")] == [pii.KIND_PHONE]
        assert [f.kind for f in pii.scan("〒 060‐0808 札幌市")] == [pii.KIND_POSTAL_CODE]

    def test_isbn_is_not_a_card(self):
        """ISBN-13 は 978/979 始まり。Luhn は 10 回に 1 回偶然通るので先頭桁で落とす。"""
        assert pii.scan("ISBN9784000000000") == []

    def test_card_prefix_required(self):
        assert not pii.has_card_prefix("9784000000000")
        assert pii.has_card_prefix("4242424242424242")
        assert pii.has_card_prefix("2221000000000009"), "Mastercard の 2 シリーズ"

    def test_phone_requires_ten_or_eleven_digits(self):
        """日本の電話番号は 10 桁か 11 桁。12 桁の枝番を電話として拾わない。"""
        assert [f for f in pii.scan("文書番号 0123-4567-8901") if f.kind == pii.KIND_PHONE] == []
        assert [f.kind for f in pii.scan("問合せ 0120-123-456")] == [pii.KIND_PHONE]

    def test_phone_in_parentheses(self):
        """便箋・封筒の印刷では市外局番を括弧で括る書式が多い。"""
        assert [f.kind for f in pii.scan("電話（03）1234-5678")] == [pii.KIND_PHONE]
        assert [f.kind for f in pii.scan("電話 03(1234)5678")] == [pii.KIND_PHONE]

    def test_document_number_is_not_a_postal_code(self):
        """「第 003-0045 号」「頁 113-0033」は目録に頻出する。郵便番号と同じ形。"""
        assert pii.scan("第 003-0045 号") == []
        assert pii.scan("頁 113-0033 参照") == []
        assert [f.kind for f in pii.scan("〒113-0033 東京都")] == [pii.KIND_POSTAL_CODE]

    def test_postal_without_hyphen_needs_the_marker(self):
        """〒 が付いていれば 7 桁続きでも郵便番号と見なしてよい。無ければ番号と区別できない。"""
        assert [f.kind for f in pii.scan("〒1130033 東京都")] == [pii.KIND_POSTAL_CODE]
        assert pii.scan("整理 1130033 番") == []

    def test_long_digit_run_is_not_sliced_into_a_card(self):
        """24 桁の数字列の先頭 16 桁が偶然 Luhn を通る、という切り出し方をしない。"""
        assert pii.scan("写真 1234 5678 9012 3456 7890 1234 番") == []

    def test_card_digits_inside_a_longer_run_are_missed_on_purpose(self):
        """逆に、長い数字列に埋もれたカード番号は取れない（docs/pii-accuracy.md に記載）。"""
        assert pii.scan("整理 12 4242424242424242") == []


class TestPlainTextExtraction:
    def test_utf8(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("日本語テキスト", encoding="utf-8")
        assert document_text.plain_text(p, max_bytes=1000) == "日本語テキスト"

    def test_shift_jis(self, tmp_path):
        """日本の現場では Shift_JIS の資料が現役。"""
        p = tmp_path / "a.txt"
        p.write_bytes("日本語テキスト".encode("cp932"))
        assert document_text.plain_text(p, max_bytes=1000) == "日本語テキスト"

    def test_binary_returns_none(self, tmp_path):
        p = tmp_path / "a.bin"
        p.write_bytes(b"\x00\x01\x02\x03")
        assert document_text.plain_text(p, max_bytes=1000) is None

    def test_truncates_at_max_bytes(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_text("a" * 10000, encoding="utf-8")
        assert len(document_text.plain_text(p, max_bytes=100)) == 100


class TestPDFDetection:
    def test_by_extension(self):
        assert document_text.is_pdf(sf("a.pdf", absolute_path=Path("/x/a.pdf")))

    def test_by_mime(self):
        assert document_text.is_pdf(sf("a.bin", mime_type="application/pdf"))

    def test_by_puid(self):
        assert document_text.is_pdf(sf("a.bin", puid="fmt/19"))

    def test_not_pdf(self):
        assert not document_text.is_pdf(sf("a.txt", puid="fmt/111", mime_type="text/plain"))

    def test_broken_pdf_returns_none_without_raising(self, tmp_path):
        """壊れた PDF は珍しくない。走査できないだけで受入自体は続ける。"""
        p = tmp_path / "broken.pdf"
        p.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
        assert document_text.pdf_text(p, max_bytes=1000) is None


class TestDFXML:
    def _root(self, files, root=Path("/in")):
        return etree.fromstring(dfxml.build(files, root, start_time=datetime(2026, 7, 25, tzinfo=UTC)))

    def test_parses_and_has_creator(self):
        root = self._root([sf("a.txt")])
        assert root.tag == "dfxml"
        assert root.findtext("creator/program") == "Archival Packager"

    def test_one_fileobject_per_file_with_1_based_ids(self):
        root = self._root([sf("a.txt"), sf("b.txt")])
        objs = root.findall("fileobject")
        assert [o.findtext("id") for o in objs] == ["1", "2"]

    def test_hash_recorded_with_algorithm(self):
        root = self._root([sf("a.txt", sha256="d" * 64)])
        h = root.find("fileobject/hashdigest")
        assert h.get("type") == "sha256"
        assert h.text == "d" * 64

    def test_hash_omitted_when_absent(self):
        root = self._root([sf("a.txt", sha256=None)])
        assert root.find("fileobject/hashdigest") is None

    @pytest.mark.parametrize("hostile", ["R&D.txt", "a<b>.txt", "quote\"x.txt", "apos'x.txt"])
    def test_hostile_filenames_survive(self, hostile):
        """Swift 版は esc() が ' を扱っていなかった。lxml なら考慮不要。"""
        root = self._root([sf(hostile)])
        assert root.findtext("fileobject/filename") == hostile

    def test_input_root_recorded(self):
        # **既定ではフォルダ名だけを残す。** 絶対パスには利用者名が入り、
        # AIP は外部に渡りうる（test_provenance_record.py を参照）。
        given = Path("/データ/移管 2026")
        root = self._root([sf("a.txt")], root=given)
        assert root.findtext("source/image_filename") == "移管 2026"
