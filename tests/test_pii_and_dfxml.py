"""PII 走査・テキスト抽出・DFXML のテスト。

PII は誤検出（偽陽性）と見落とし（偽陰性）の両方が問題になる。
偽陽性が多いと「毎回警告が出るもの」として無視されるようになり、
結果的に本物の検出も見落とされる。両方向を固定する。

画像の技術的特性（scan.image_characteristics）もここに置いてある。
出力先が DFXML であり、**読めなかったときに読めなかったと分かること**という
固定したい点が PII 走査とまったく同じだから。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import dfxml, document_text, pii, scan
from archival_packager.core.models import ImageCharacteristics, ScannedFile


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


def png(path: Path, size=(4, 2), mode="RGB", **save_kw) -> Path:
    """テスト用の小さな画像を書く。"""
    from PIL import Image

    Image.new(mode, size).save(path, **save_kw)
    return path


class TestImageCharacteristics:
    """画像そのものの性質を読めること（Issue #8）。

    DFXML にはファイル単位の事実しか無く、**画素数も色空間も残っていなかった**。
    画素数の分からない画像は、後から見た人に「これで原本の代わりになるか」を
    判断させられない。ここで固定するのは「読めること」だけでなく、
    **読めなかったときに読めなかったと分かること**（このリポジトリで最も
    避けたいのは沈黙する失敗）。
    """

    def test_reads_pixels_colour_space_and_depth(self, tmp_path):
        c = scan.image_characteristics(png(tmp_path / "a.png", size=(7, 3)))
        assert (c.width, c.height) == (7, 3)
        assert c.color_space == "RGB"
        assert c.bits_per_sample == 8
        assert c.readable

    def test_bilevel_image_is_one_bit(self, tmp_path):
        """白黒 2 値は 1 ビット。

        Pillow は mode "1" を内部で 1 画素 1 バイトに展開して持つので、
        型情報をそのまま信じると 8 ビットと書いてしまう。記録したいのは
        メモリ上の持ち方ではなく画像そのものの性質のほう。
        """
        c = scan.image_characteristics(png(tmp_path / "a.png", mode="1"))
        assert c.bits_per_sample == 1

    def test_resolution_is_recorded_when_the_file_has_one(self, tmp_path):
        c = scan.image_characteristics(png(tmp_path / "a.png", dpi=(300, 300)))
        # PNG は解像度を「1 メートルあたりの画素数」の整数で持つため、
        # 300 dpi を書いても 300 ちょうどには戻らない。**丸めない。**
        # 原本に入っている値をそのまま残す。
        assert c.x_dpi is not None and 299 < c.x_dpi < 301
        assert c.y_dpi is not None and 299 < c.y_dpi < 301

    def test_resolution_is_not_invented(self, tmp_path):
        """解像度を持たない画像に 72 dpi を補わないこと。

        補うと「原本が解像度を持っていなかった」という事実が記録から消え、
        後から見た人には本当に 72 dpi だったのか区別が付かない。
        """
        c = scan.image_characteristics(png(tmp_path / "a.png"))
        assert c.x_dpi is None and c.y_dpi is None

    def test_broken_image_is_recorded_as_unreadable(self, tmp_path):
        """壊れた画像で例外を投げないこと、かつ黙って飛ばさないこと。

        壊れた画像は資料の中に普通に混ざっている。そこで移管全体を止めるのは
        割に合わないが、「特性が空の画像」と見分けが付かなくなるのはもっと悪い。
        """
        p = tmp_path / "broken.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"not really a png")
        c = scan.image_characteristics(p)
        assert not c.readable
        assert c.error

    def test_unreadable_reason_does_not_leak_the_absolute_path(self, tmp_path):
        """読めなかった理由に絶対パスを残さないこと。

        Pillow の例外文にはファイルの絶対パスが入り、そこには利用者名が入る
        （C:\\Users\\<名前>\\…）。この文字列は dfxml.xml に入り、パッケージは
        外部に渡りうる。dfxml.build が image_filename に絶対パスを既定で
        書かないのと同じ理由で、ここでも落とす。
        """
        p = tmp_path / "broken.png"
        p.write_bytes(b"nonsense bytes here")  # Pillow は絶対パス入りの文で断る
        error = scan.image_characteristics(p).error
        assert error and str(tmp_path) not in error
        assert p.name in error, "ファイル名は残す（どれの話か分からなくなるため）"

    def test_a_relative_path_does_not_mangle_the_reason(self, tmp_path, monkeypatch):
        """相対パスで呼ばれても、理由の文を壊さないこと。

        絶対パスを隠す処理が、親ディレクトリの文字列をそのまま置き換えていた。
        相対パスだと親が "." になるため、文中のピリオドが全部置き換わり
        "cannot identify image file \'sig…png\'" という読めない記録になっていた。
        """
        monkeypatch.chdir(tmp_path)
        Path("sig.png").write_bytes(b"nonsense bytes here")
        error = scan.image_characteristics(Path("sig.png")).error
        assert "sig.png" in error, error

    def test_the_original_is_not_modified(self, tmp_path):
        """**原本を書き換えない。** このアプリが手放してはいけない性質。

        Pillow は書き込みもできる道具なので、読むだけのつもりが save に
        なっていないことを入口ごとに確かめる。
        """
        p = png(tmp_path / "a.png")
        before = (p.read_bytes(), p.stat().st_mtime_ns)
        scan.image_characteristics(p)
        assert (p.read_bytes(), p.stat().st_mtime_ns) == before


class TestDFXMLImageCharacteristics:
    """読み取った特性が DFXML に入ること（Issue #8）。"""

    AP = dfxml.AP_NS

    def _root(self, files):
        return etree.fromstring(
            dfxml.build(files, Path("/in"), start_time=datetime(2026, 7, 25, tzinfo=UTC))
        )

    def _image_el(self, root):
        return root.find(f"fileobject/{{{self.AP}}}image")

    def test_values_are_written(self):
        root = self._root([
            sf("a.png", image=ImageCharacteristics(
                width=7, height=3, color_space="RGB", bits_per_sample=8,
                x_dpi=300.0, y_dpi=300.0,
            ))
        ])
        el = self._image_el(root)
        got = {etree.QName(child).localname: child.text for child in el}
        assert got == {
            "width": "7", "height": "3", "color_space": "RGB",
            "bits_per_sample": "8", "x_dpi": "300", "y_dpi": "300",
        }

    def test_missing_values_are_omitted_not_emptied(self):
        """取れなかった項目は要素ごと出さない。

        空の要素を出すと「0 だった」「空文字だった」と読まれる余地が残る。
        """
        root = self._root([sf("a.png", image=ImageCharacteristics(width=7, height=3))])
        names = [etree.QName(child).localname for child in self._image_el(root)]
        assert names == ["width", "height"]

    def test_unreadable_image_says_so_with_a_reason(self):
        """読めなかったことが DFXML からも分かること。

        特性が空なだけだと「画像だが情報を持っていなかった」と読めてしまう。
        """
        root = self._root([sf("a.png", image=ImageCharacteristics(error="OSError: 壊れています"))])
        el = self._image_el(root)
        assert el.get("readable") == "false"
        assert el.findtext(f"{{{self.AP}}}error") == "OSError: 壊れています"

    def test_readable_is_stated_explicitly(self):
        """読めた場合も readable を書く。

        属性が無いことを「読めた」と解釈させると、書き忘れと区別が付かない。
        """
        root = self._root([sf("a.png", image=ImageCharacteristics(width=1, height=1))])
        assert self._image_el(root).get("readable") == "true"

    def test_image_element_comes_last_in_the_fileobject(self):
        """**fileobject の末尾に置くこと。**

        DFXML が別の名前空間の要素を許しているのは fileobject の内容モデルの
        末尾だけ（`<xs:any namespace="##other">`）。順序を変えると、DFXML の
        スキーマで検証した人のところで落ちる。
        """
        root = self._root([sf("a.png", sha256="d" * 64,
                              image=ImageCharacteristics(width=1, height=1))])
        children = [etree.QName(child).localname for child in root.find("fileobject")]
        assert children[-1] == "image"
        assert children[-2] == "hashdigest"

    def test_the_element_is_not_in_the_dfxml_vocabulary(self):
        """DFXML 自身の語彙を勝手に増やさないこと。

        画素数に当たる要素は DFXML に無い。名前空間を分けずに書くと、
        DFXML の要素のふりをした別物になる。
        """
        root = self._root([sf("a.png", image=ImageCharacteristics(width=1, height=1))])
        assert self._image_el(root) is not None
        assert root.find("fileobject/image") is None

    def test_the_reading_tool_is_recorded_once(self):
        """どの版の Pillow が言ったことなのかを残す。

        読み取れる値は道具の版で変わりうる。fileobject ごとには書かない
        （数万件ぶん同じ文字列が並ぶだけになる）。
        """
        import PIL

        root = self._root([sf("a.png", image=ImageCharacteristics(width=1, height=1))])
        libraries = root.findall("creator/library")
        assert len(libraries) == 1
        assert libraries[0].get("name") == "Pillow"
        assert libraries[0].get("version") == PIL.__version__

    def test_nothing_is_added_when_no_image_was_examined(self):
        """画像を 1 件も見ていない移管では、何も足さないこと。

        使っていない道具を来歴に書くと、読んだ人は「Pillow で何かした」と読む。
        """
        xml = dfxml.build([sf("a.txt")], Path("/in"),
                          start_time=datetime(2026, 7, 25, tzinfo=UTC)).decode("utf-8")
        assert dfxml.AP_NS not in xml
        assert "<library" not in xml
