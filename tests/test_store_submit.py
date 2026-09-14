"""ストア申請スクリプトの検査。

なぜこのテストがあるか
----------------------
2026-09-12、`apply_listing` が言語キーを `ja` と決め打ちしていた。
実際の申請は `ja-jp` だったので、そのまま送ると既存の掲載情報は更新されず、
空の `ja` が足されるだけになる。説明文が消えたまま公開されるところだった。
API を叩かずに確かめられる部分は、ここで押さえておく。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "store_submit", ROOT / "scripts" / "store_submit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store_submit = _load()


def _listing() -> dict:
    return {"description": "説明", "short": "短い説明", "keywords": ["a", "b"]}


class TestListingKeys:
    def test_finds_ja_jp(self):
        sub = {"listings": {"ja-jp": {"baseListing": {}}}}
        assert store_submit.listing_keys(sub, "ja") == ["ja-jp"]

    def test_finds_bare_ja(self):
        sub = {"listings": {"ja": {"baseListing": {}}}}
        assert store_submit.listing_keys(sub, "ja") == ["ja"]

    def test_ignores_other_languages(self):
        sub = {"listings": {"en-us": {"baseListing": {}},
                            "ja-jp": {"baseListing": {}}}}
        assert store_submit.listing_keys(sub, "ja") == ["ja-jp"]
        assert store_submit.listing_keys(sub, "en") == ["en-us"]

    def test_falls_back_when_absent(self):
        assert store_submit.listing_keys({"listings": {}}, "ja") == ["ja-jp"]
        assert store_submit.listing_keys({"listings": {}}, "en") == ["en-us"]


class TestApplyListings:
    def test_updates_existing_ja_jp_and_adds_no_ja(self):
        sub = {"listings": {"ja-jp": {"baseListing": {"description": "古い",
                                                      "title": "残す"}}}}
        store_submit.apply_listings(sub, {"ja": _listing()})

        base = sub["listings"]["ja-jp"]["baseListing"]
        assert base["description"] == "説明"
        assert base["shortDescription"] == "短い説明"
        assert base["keywords"] == ["a", "b"]
        # 既にある項目は消さない
        assert base["title"] == "残す"
        # 空の言語を増やさない
        assert "ja" not in sub["listings"]

    def test_creates_the_english_listing_when_absent(self):
        """英語の掲載情報は 0.1.7 が最初。無いところに作れること。"""
        sub = {"listings": {"ja-jp": {"baseListing": {}}}}
        store_submit.apply_listings(sub, {"en": _listing()})
        assert sub["listings"]["en-us"]["baseListing"]["description"] == "説明"

    def test_leaves_languages_we_do_not_manage_alone(self):
        """LISTINGS に無い言語の掲載情報は消さない。"""
        sub = {"listings": {"ko-kr": {"baseListing": {"description": "한국어"}},
                            "ja-jp": {"baseListing": {}}}}
        store_submit.apply_listings(sub, {"ja": _listing()})
        assert sub["listings"]["ko-kr"]["baseListing"]["description"] == "한국어"

    def test_does_not_touch_pricing(self):
        sub = {"listings": {"ja-jp": {"baseListing": {}}},
               "pricing": {"priceId": "Free"}}
        store_submit.apply_listings(sub, {"ja": _listing()})
        assert sub["pricing"] == {"priceId": "Free"}


class TestListingFromMarkdown:
    """0.1.0 では開発者名が抜けたまま公開された。正本から取れているか見る。"""

    def test_every_declared_language_has_a_file(self):
        for lang, path in store_submit.LISTINGS.items():
            assert path.is_file(), f"{lang} の正本がありません: {path}"

    def test_has_both_developers(self):
        for lang, path in store_submit.LISTINGS.items():
            listing = store_submit.listing_from_markdown(path)
            for name in store_submit.DEVELOPER_NAMES[lang]:
                assert name in listing["description"], (lang, name)

    def test_within_store_limits(self):
        for path in store_submit.LISTINGS.values():
            listing = store_submit.listing_from_markdown(path)
            assert 0 < len(listing["description"]) <= 10000
            assert 0 < len(listing["short"]) <= 1000
            assert len(listing["keywords"]) <= 7

    def test_the_manifest_declares_every_listing_language(self):
        """掲載情報を置ける言語は、MSIX が宣言しているものだけ。

        宣言していない言語の掲載情報を送ると拒まれる。**片方だけ足すと、
        送ってみるまで分からない**ので、ここで突き合わせる。
        """
        manifest = (ROOT / "packaging" / "windows"
                    / "AppxManifest.xml.in").read_text(encoding="utf-8")
        for lang in store_submit.LISTINGS:
            key = store_submit.DEFAULT_LISTING_KEYS[lang]
            tag = key.split("-")[0] + "-" + key.split("-")[1].upper()
            assert f'<Resource Language="{tag}" />' in manifest, tag


class TestThrottlingIsRetried:
    """403 を「権限が無い」と決めつけない。

    2026-09-12 と 09-13、続けて叩いたときに本文の空の 403 が返った。
    いずれも直前の呼び出しは成功していて、数分置くと元に戻っている。
    役割の設定を疑って調べ直すと時間を失うので、まず数回やり直す。
    """

    def test_retries_then_succeeds(self, monkeypatch):
        import urllib.error

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

            class R:
                def read(self):
                    return b'{"ok": true}'

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()

        monkeypatch.setattr(store_submit.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(store_submit.time, "sleep", lambda _s: None)
        assert store_submit._request("GET", "https://example.test/x") == {"ok": True}
        assert len(calls) == 3

    def test_gives_up_and_says_to_check_the_role(self, monkeypatch):
        """やり直しても駄目なら、そこで初めて権限の話にする。"""
        import urllib.error

        def always_403(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

        monkeypatch.setattr(store_submit.urllib.request, "urlopen", always_403)
        monkeypatch.setattr(store_submit.time, "sleep", lambda _s: None)
        with pytest.raises(store_submit.StoreError, match="Manager"):
            store_submit._request("GET", "https://example.test/x")

    def test_other_errors_are_not_retried(self, monkeypatch):
        """400 は何度送っても 400。待つだけ無駄で、原因も隠れる。"""
        import urllib.error

        calls = []

        def bad_request(req, timeout=None):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)

        monkeypatch.setattr(store_submit.urllib.request, "urlopen", bad_request)
        monkeypatch.setattr(store_submit.time, "sleep", lambda _s: None)
        with pytest.raises(store_submit.StoreError, match="400"):
            store_submit._request("GET", "https://example.test/x")
        assert len(calls) == 1


class TestCreateSubmissionRefusesWhenPending:
    """ダッシュボードと API を混ぜると壊れる。作りかけがあるなら止まること。"""

    def test_raises(self, monkeypatch):
        calls = []

        def fake(method, url, **kwargs):
            calls.append((method, url))
            if method == "GET":
                return {"pendingApplicationSubmission": {"id": "123"}}
            pytest.fail("作りかけがあるのに POST してはいけない")

        monkeypatch.setattr(store_submit, "_request", fake)
        with pytest.raises(store_submit.StoreError, match="保留中"):
            store_submit.create_submission("tok", "STOREID")
        assert [m for m, _ in calls] == ["GET"]

class TestEnsureDeviceFamilies:
    """複製した申請は端末種別が欠けていることがある。

    2026-09-12、Desktop の分しか入っておらず、確定の直前に 400 で落ちた:
      AllowTargetFutureDeviceFamilies needs to be initialized for all
      supported platform, [Desktop, Mobile, Xbox, Holographic]
    108 MB のアップロードを終えた後で落ちるので、事前に埋める。
    """

    def test_fills_missing_families(self):
        sub = {"allowTargetFutureDeviceFamilies": {"Desktop": True}}
        store_submit.ensure_device_families(sub)
        assert sub["allowTargetFutureDeviceFamilies"] == {
            "Desktop": True, "Mobile": False, "Xbox": False, "Holographic": False}

    def test_keeps_families_we_do_not_know_about(self):
        """複製元にある種別を消さない。

        エラーが名指しするのは 4 つだが、公式の申請オブジェクトの例には
        `Team` を含む 5 種類が載っている。4 つで作り直すと `Team` が黙って消え、
        こちらの知らない設定を勝手に落とすことになる（2026-09-12 の指摘）。
        """
        sub = {"allowTargetFutureDeviceFamilies": {"Desktop": True, "Team": True}}
        store_submit.ensure_device_families(sub)
        got = sub["allowTargetFutureDeviceFamilies"]
        assert got["Team"] is True
        assert set(got) == {"Desktop", "Team", "Mobile", "Xbox", "Holographic"}

    def test_fills_when_absent(self):
        sub = {}
        store_submit.ensure_device_families(sub)
        assert set(sub["allowTargetFutureDeviceFamilies"]) == set(
            store_submit.REQUIRED_DEVICE_FAMILIES)

    def test_does_not_duplicate_a_family_that_differs_only_in_case(self):
        """大文字小文字だけ違うキーを二重に持たせない。

        返ってきたキーは**書き換えない**（こちらが知らない表記を勝手に直すと、
        相手の期待とずれたときに原因が追えなくなる）。足りないものだけ足す。
        """
        sub = {"allowTargetFutureDeviceFamilies": {"desktop": True, "XBOX": True}}
        store_submit.ensure_device_families(sub)
        got = sub["allowTargetFutureDeviceFamilies"]
        assert got["desktop"] is True          # 元のキーのまま残る
        assert got["XBOX"] is True
        assert "Desktop" not in got            # 二重に生やさない
        assert "Xbox" not in got
        assert got["Mobile"] is False          # 足りないものは足す

    def test_does_not_invent_true(self):
        """デスクトップ専用のアプリを、勝手に他の端末に広げない。"""
        sub = {"allowTargetFutureDeviceFamilies": {}}
        store_submit.ensure_device_families(sub)
        assert not any(sub["allowTargetFutureDeviceFamilies"].values())


class TestResumeSubmission:
    def test_raises_when_nothing_pending(self):
        with pytest.raises(store_submit.StoreError, match="進行中の申請がありません"):
            store_submit.resume_submission("tok", "STOREID", {})

    def test_reads_the_pending_one(self, monkeypatch):
        seen = {}

        def fake(method, url, **kwargs):
            seen["method"] = method
            seen["url"] = url
            return {"id": "999"}

        monkeypatch.setattr(store_submit, "_request", fake)
        app = {"pendingApplicationSubmission": {"id": "999"}}
        got = store_submit.resume_submission("tok", "STOREID", app)
        assert got["id"] == "999"
        assert seen["method"] == "GET"
        assert seen["url"].endswith("/applications/STOREID/submissions/999")
