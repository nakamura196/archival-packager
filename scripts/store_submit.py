"""Microsoft ストアの申請を API で行う。

なぜスクリプトにするか
----------------------
ダッシュボードでの申請は 15 分かかり、手で 10 か所ほど触る。
直したものをすぐ配りたいときに、その 15 分が足かせになる。
また、**説明文を貼り忘れる**（実際に 0.1.0 で開発者名が抜けた）。
手順を文章で残すより、実行できる形にしたほうが確実。

流れ（Microsoft Store submission API v1）
-----------------------------------------
    1. Entra ID からトークンを取る
    2. 前回の申請を複製して新しい申請を作る
    3. 説明文などを差し替える
    4. MSIX を zip に入れて Azure へアップロードする
    5. 申請 JSON を書き戻してコミットする
    6. 状態を見届ける

使い方
------
    op run --env-file=store/.env -- python scripts/store_submit.py --msix ~/Downloads/ArchivalPackager.msix

    # 何が起きるかだけ見る（送信しない）
    op run --env-file=store/.env -- python scripts/store_submit.py --dry-run

前提
----
store/API設定手順.md のとおり、Entra のテナントとアプリ登録を作ってあること。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/token"
RESOURCE = "https://manage.devcenter.microsoft.com"
API = "https://manage.devcenter.microsoft.com/v1.0/my"

# 申請 JSON で値を埋めておく必要がある端末種別。
# 複製で返ってくる申請には一部しか入っておらず、足りないまま PUT すると
# 400 InvalidParameterValue で落ちる（2026-09-12 に遭遇）。
#: エラーが名指しで要求してくる端末種別。**この 4 つは必ず入れる。**
REQUIRED_DEVICE_FAMILIES = ("Desktop", "Mobile", "Xbox", "Holographic")

ROOT = Path(__file__).resolve().parent.parent

#: 言語コード -> 正本の markdown。**ここに足せば、その言語も送られる。**
#: 掲載情報を置けるのは、MSIX が宣言している言語だけ
#: （packaging/windows/AppxManifest.xml.in の <Resource Language=...>）。
#: 宣言していない言語を送ると拒まれるので、増やすときは両方を直すこと。
LISTINGS = {
    "ja": ROOT / "store" / "listing-ja.md",
    "en": ROOT / "store" / "listing-en.md",
}

#: 言語コード -> 申請 JSON の listings で使う既定の鍵。
#: 既に申請に入っている鍵があれば、そちらを優先する（下の listing_keys）。
DEFAULT_LISTING_KEYS = {"ja": "ja-jp", "en": "en-us"}

#: 説明文に必ず入っていてほしい開発者名。0.1.0 でこれが抜けたまま公開した。
DEVELOPER_NAMES = {"ja": ("中村", "金"), "en": ("Nakamura", "Kim")}


class StoreError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


#: 403 が返ったときに待つ秒数。**回数と間隔は控えめにする。**
#: 絞り込みに対して急いで叩き直すのは、絞り込みを深めるだけになりうる。
_THROTTLE_WAITS = (60, 120, 240)


def _request(method: str, url: str, *, token: str | None = None,
             body: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
    """API を 1 回叩く。403 は絞り込みとみなし、間を置いて数回だけやり直す。

    **403 は「権限が無い」とは限らない。** このアプリでは、続けて叩いたときに
    本文の空の 403 が返る（2026-09-12 と 09-13 に 2 回。いずれも直前の呼び出しは
    成功していて、数分置くと元に戻った）。役割の設定を疑って調べ直すと時間を失う。
    やり直しても駄目なときだけ、権限の話として扱う。
    """
    for attempt, wait in enumerate((*_THROTTLE_WAITS, None)):
        req = urllib.request.Request(url, method=method, data=body)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=120) as res:
                raw = res.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:800]
            if exc.code in (403, 429) and wait is not None:
                print(f"  {exc.code} が返りました。{wait} 秒待って {attempt + 2} 回目を試します"
                      f"（続けて叩くと返ることがあります）", flush=True)
                time.sleep(wait)
                continue
            hint = ""
            if exc.code == 403:
                hint = ("\n  やり直しても 403 でした。ここで初めて役割（Manager）と"
                        "テナントの結び付きを疑ってください。")
            raise StoreError(
                f"{method} {url} が {exc.code} で失敗しました:\n{detail}{hint}") from exc
        return json.loads(raw) if raw else {}
    raise StoreError("到達しません")  # pragma: no cover


def token_for(tenant: str, client_id: str, secret: str) -> str:
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": secret,
        "resource": RESOURCE,
    }).encode()
    req = urllib.request.Request(TOKEN_URL.format(tenant=tenant), data=body)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read())["access_token"]
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise StoreError(f"トークンを取れませんでした:\n{detail}") from exc


# --------------------------------------------------------------------------
# 掲載情報
# --------------------------------------------------------------------------


def listing_from_markdown(path: Path) -> dict[str, str]:
    """掲載情報の markdown から、貼るべき文面を取り出す。

    **思い出しながら書き直さない。** 正本はこれらの markdown に置き、ここから送る。
    節の見出しは日本語で揃えてある（英語版も同じ）。運用する人が読むための
    見出しであって、送られるのは各節の中身だけ。
    """
    text = path.read_text(encoding="utf-8")

    def section(name: str) -> str:
        marker = f"\n## {name}"
        if marker not in text:
            raise StoreError(f"{path.name} に「{name}」の節がありません")
        body = text.split(marker, 1)[1]
        body = body.split("\n## ", 1)[0]
        # ストアの説明欄は素のテキスト。小見出し（### できること）は
        # 印を外して 1 行として残す。落とすと箇条書きが何の一覧か分からなくなる。
        lines = []
        for ln in body.splitlines():
            if ln.startswith("###"):
                ln = ln.lstrip("# ").rstrip()
            # ストアの説明欄は素のテキスト。** は装飾として表示されない。
            lines.append(ln.replace("**", ""))
        return "\n".join(lines).strip()

    return {
        "description": section("説明（Description）"),
        "short": section("簡単な説明（Short description・最大 1000 文字）"),
        "keywords": [ln.strip() for ln in section("検索キーワード（最大 7 つ）").splitlines()
                     if ln.strip()],
    }


# --------------------------------------------------------------------------
# 申請
# --------------------------------------------------------------------------


def create_submission(token: str, store_id: str) -> dict:
    app = _request("GET", f"{API}/applications/{store_id}", token=token)
    if app.get("pendingApplicationSubmission"):
        raise StoreError(
            "保留中の申請があります。ダッシュボードで作りかけの申請を削除してから"
            "やり直してください（ダッシュボードと API は混ぜられません）。"
        )
    return _request("POST", f"{API}/applications/{store_id}/submissions", token=token)


def ensure_device_families(submission: dict) -> dict:
    """端末種別の指定を全部そろえる。

    複製した申請には Desktop の分しか入っていないことがある。
    そのまま送ると次のように断られる。

        AllowTargetFutureDeviceFamilies needs to be initialized for all
        supported platform, [Desktop, Mobile, Xbox, Holographic]

    **既に入っている種別は消さない。** 公式ドキュメントの申請オブジェクトの例には
    `Team` を含む 5 種類が載っており、エラーが名指しする 4 つで dict を作り直すと、
    複製元に入っていた `Team` を黙って削ることになる。
    足りないものを足すだけにして、あるものはそのまま通す（2026-09-12 に修正）。

    無いものは False で埋める。このアプリはデスクトップ専用なので、勝手に True にしない。
    """
    current = submission.get("allowTargetFutureDeviceFamilies") or {}
    filled = {str(k): bool(v) for k, v in current.items()}
    seen = {k.lower() for k in filled}
    for family in REQUIRED_DEVICE_FAMILIES:
        if family.lower() not in seen:
            filled[family] = False
    submission["allowTargetFutureDeviceFamilies"] = filled
    return submission


def resume_submission(token: str, store_id: str, app: dict) -> dict:
    """進行中の申請を引き継ぐ。

    確定の途中で落ちたとき、作り直すとパッケージを上げ直すことになる。
    108 MB を二度送らずに済むよう、既にある申請を読んで続きから行う。
    """
    node = app.get("pendingApplicationSubmission")
    if not node:
        raise StoreError("進行中の申請がありません。--resume を外して実行してください。")
    sid = node["id"]
    return _request("GET", f"{API}/applications/{store_id}/submissions/{sid}",
                    token=token)


def listing_keys(submission: dict, lang: str) -> list[str]:
    """申請の中で、その言語の掲載情報が入っている鍵を返す。

    **`ja` と決め打ちしない。** 実際の申請は `ja-jp` だった。
    決め打ちすると、既存の掲載情報を更新せず空の `ja` を足してしまい、
    説明文が消えたまま公開される。

    その言語の掲載情報がまだ無ければ、既定の鍵を 1 つ返す（新しく作る）。
    """
    keys = [k for k in submission.get("listings", {})
            if k.lower() == lang or k.lower().startswith(f"{lang}-")]
    return keys or [DEFAULT_LISTING_KEYS[lang]]


def apply_listings(submission: dict, listings_by_lang: dict[str, dict]) -> dict:
    """掲載情報を言語ごとに差し替える。価格や年齢区分には触らない。

    **知らない言語の掲載情報は消さない。** 触るのは LISTINGS にある言語だけ。
    """
    listings = submission.setdefault("listings", {})
    for lang, listing in listings_by_lang.items():
        for key in listing_keys(submission, lang):
            base = listings.setdefault(key, {}).setdefault("baseListing", {})
            base["description"] = listing["description"]
            base["shortDescription"] = listing["short"]
            base["keywords"] = listing["keywords"]
    return submission


def stage_package(submission: dict, msix: Path, work: Path) -> Path:
    """新しい MSIX を足し、古いものに削除の印を付けて、zip に固める。"""
    packages = submission.setdefault("applicationPackages", [])
    for p in packages:
        p["fileStatus"] = "PendingDelete"
    packages.append({"fileName": msix.name, "fileStatus": "PendingUpload"})

    bundle = work / "package.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(msix, arcname=msix.name)
    return bundle


def upload(url: str, bundle: Path) -> None:
    data = bundle.read_bytes()
    req = urllib.request.Request(url.replace("+", "%2B"), method="PUT", data=data)
    req.add_header("x-ms-blob-type", "BlockBlob")
    try:
        with urllib.request.urlopen(req, timeout=1800) as res:
            if res.status not in (200, 201):
                raise StoreError(f"アップロードが {res.status} で終わりました")
    except urllib.error.HTTPError as exc:
        raise StoreError(f"アップロードに失敗しました: {exc.code}") from exc


def commit(token: str, store_id: str, submission: dict) -> None:
    sid = submission["id"]
    _request("PUT", f"{API}/applications/{store_id}/submissions/{sid}",
             token=token, body=json.dumps(submission).encode())
    _request("POST", f"{API}/applications/{store_id}/submissions/{sid}/commit", token=token)


def wait(token: str, store_id: str, submission_id: str, *, minutes: int = 30) -> str:
    """状態を見届ける。審査そのものは数日かかるので、受理までを見る。"""
    url = f"{API}/applications/{store_id}/submissions/{submission_id}/status"
    for _ in range(minutes * 4):
        status = _request("GET", url, token=token)
        state = status.get("status", "")
        print(f"  {state}", flush=True)
        if state in ("CommitFailed", "PreProcessingFailed", "CertificationFailed",
                     "Release", "Published"):
            details = status.get("statusDetails", {})
            for kind in ("errors", "warnings"):
                for item in details.get(kind) or []:
                    print(f"    {kind}: {item.get('code')} {item.get('details')}")
            return state
        if state in ("PendingCommit", "CommitStarted", "PreProcessing",
                     "Certification", "PendingPublication", "Publishing"):
            time.sleep(15)
            continue
        time.sleep(15)
    return "（時間内に終わりませんでした。ダッシュボードで確認してください）"


# --------------------------------------------------------------------------


def check_credentials() -> int:
    """資格情報が通るかだけ確かめる。読むだけで、申請には触れない。

    --dry-run は説明文を組み立てるだけなので、鍵が正しいかは分からない。
    鍵を入れ替えたあと、申請を始める前にここで一度通しておく。
    """
    missing = [k for k in ("STORE_TENANT_ID", "STORE_CLIENT_ID",
                           "STORE_CLIENT_SECRET", "STORE_ID")
               if not os.environ.get(k)]
    if missing:
        print(f"環境変数がありません: {', '.join(missing)}。"
              f"op run --env-file=store/.env -- で実行してください。", file=sys.stderr)
        return 1

    store_id = os.environ["STORE_ID"]
    try:
        token = token_for(os.environ["STORE_TENANT_ID"],
                          os.environ["STORE_CLIENT_ID"],
                          os.environ["STORE_CLIENT_SECRET"])
    except StoreError as exc:
        print(f"トークンを取れませんでした。\n{exc}", file=sys.stderr)
        return 1
    print("トークンを取れました。")

    try:
        app = _request("GET", f"{API}/applications/{store_id}", token=token)
    except StoreError as exc:
        print(f"アプリを読めませんでした。役割が Manager か確かめてください。\n{exc}",
              file=sys.stderr)
        return 1

    print(f"アプリを読めました: {app.get('primaryName')} ({store_id})")
    pending = app.get("pendingApplicationSubmission")
    last = app.get("lastPublishedApplicationSubmission")
    print(f"  公開済みの申請: {(last or {}).get('id', 'なし')}")
    print(f"  進行中の申請  : {(pending or {}).get('id', 'なし')}")
    return 0


def watch_pending() -> int:
    """進行中の申請の状態だけを見届ける。**何も変えない。**

    確定したあと状態の問い合わせで落ちると、それまでは手立てが無かった。
    `--resume` は掲載情報を当て直して確定もやり直すので、既に確定した申請には
    使えない。2026-09-12、確定の直後に status が 403 を返し、
    「送ったが、どうなったか分からない」状態になった。読むだけの入口を分けておく。
    """
    missing = [k for k in ("STORE_TENANT_ID", "STORE_CLIENT_ID",
                           "STORE_CLIENT_SECRET", "STORE_ID")
               if not os.environ.get(k)]
    if missing:
        print(f"環境変数がありません: {', '.join(missing)}。"
              f"op run --env-file=store/.env -- で実行してください。", file=sys.stderr)
        return 1

    store_id = os.environ["STORE_ID"]
    token = token_for(os.environ["STORE_TENANT_ID"],
                      os.environ["STORE_CLIENT_ID"],
                      os.environ["STORE_CLIENT_SECRET"])
    app = _request("GET", f"{API}/applications/{store_id}", token=token)
    pending = (app.get("pendingApplicationSubmission") or {}).get("id")
    if not pending:
        print("進行中の申請はありません（公開済みです）。")
        return 0

    print(f"進行中の申請 {pending} の状態を見ます…")
    state = wait(token, store_id, pending)
    print(f"結果: {state}")
    return 0 if state not in ("CommitFailed", "PreProcessingFailed",
                              "CertificationFailed") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msix", type=Path, help="差し替える MSIX。省略すると掲載情報だけ更新")
    parser.add_argument("--dry-run", action="store_true", help="送信せず、何を送るかだけ出す")
    parser.add_argument("--check", action="store_true",
                        help="資格情報だけ確かめる。読むだけで、何も変えない")
    parser.add_argument("--resume", action="store_true",
                        help="進行中の申請を引き継ぐ。確定の途中で落ちたとき用")
    parser.add_argument("--skip-upload", action="store_true",
                        help="パッケージの送信を省く。既に上げ終わっているとき用")
    parser.add_argument("--status", action="store_true",
                        help="進行中の申請の状態だけ見届ける。読むだけで、何も変えない")
    args = parser.parse_args()

    if args.check:
        return check_credentials()

    if args.status:
        return watch_pending()

    listings_by_lang = {lang: listing_from_markdown(path)
                        for lang, path in LISTINGS.items()}
    print("掲載情報を読みました:")
    for lang, listing in listings_by_lang.items():
        print(f"  [{lang}] 説明 {len(listing['description'])} 文字 / "
              f"簡単な説明 {len(listing['short'])} 文字 / "
              f"キーワード {len(listing['keywords'])} 件")
        # **開発者名が抜けたまま公開したことがある**（0.1.0）。言語ごとに確かめる。
        names = DEVELOPER_NAMES[lang]
        if any(n not in listing["description"] for n in names):
            print(f"  ※ [{lang}] 説明に開発者名が入っていません", file=sys.stderr)

    if args.dry_run:
        for lang, listing in listings_by_lang.items():
            print(f"\n--- 送る説明文 [{lang}] ---")
            print(listing["description"])
        return 0

    missing = [k for k in ("STORE_TENANT_ID", "STORE_CLIENT_ID",
                           "STORE_CLIENT_SECRET", "STORE_ID")
               if not os.environ.get(k)]
    if missing:
        print(f"環境変数がありません: {', '.join(missing)}。"
              f"op run --env-file=store/.env -- で実行してください。", file=sys.stderr)
        return 1

    store_id = os.environ["STORE_ID"]
    token = token_for(os.environ["STORE_TENANT_ID"],
                      os.environ["STORE_CLIENT_ID"],
                      os.environ["STORE_CLIENT_SECRET"])

    if args.resume:
        app = _request("GET", f"{API}/applications/{store_id}", token=token)
        submission = resume_submission(token, store_id, app)
        print(f"進行中の申請を引き継ぎます（id={submission['id']}）…")
    else:
        print("前回の申請を複製しています…")
        submission = create_submission(token, store_id)

    submission = apply_listings(submission, listings_by_lang)
    submission = ensure_device_families(submission)

    if args.msix:
        if not args.msix.is_file():
            print(f"MSIX が見つかりません: {args.msix}", file=sys.stderr)
            return 1
        work = Path(os.environ.get("TMPDIR", "/tmp")) / "archival-packager-store"
        work.mkdir(parents=True, exist_ok=True)
        bundle = stage_package(submission, args.msix, work)
        if args.skip_upload:
            print("パッケージは送信済みとして扱います（--skip-upload）。")
        else:
            print(f"パッケージを送っています（{bundle.stat().st_size // 1024 // 1024} MB）…")
            upload(submission["fileUploadUrl"], bundle)
    elif args.skip_upload:
        print("--skip-upload は --msix と一緒に使ってください。", file=sys.stderr)
        return 1

    print("申請を確定しています…")
    commit(token, store_id, submission)
    state = wait(token, store_id, submission["id"])
    print(f"結果: {state}")
    return 0 if state not in ("CommitFailed", "PreProcessingFailed",
                              "CertificationFailed") else 1


if __name__ == "__main__":
    sys.exit(main())
