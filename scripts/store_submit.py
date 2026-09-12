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
    op run --env-file=store/.env -- python scripts/store_submit.py \
        --msix ~/Downloads/archival-packager-0.1.6/ArchivalPackager.msix

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

ROOT = Path(__file__).resolve().parent.parent
LISTING = ROOT / "store" / "listing-ja.md"


class StoreError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


def _request(method: str, url: str, *, token: str | None = None,
             body: bytes | None = None, headers: dict[str, str] | None = None) -> dict:
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
        raise StoreError(f"{method} {url} が {exc.code} で失敗しました:\n{detail}") from exc
    return json.loads(raw) if raw else {}


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


def listing_from_markdown() -> dict[str, str]:
    """store/listing-ja.md から、貼るべき文面を取り出す。

    **思い出しながら書き直さない。** 正本はこの 1 枚に置き、ここから送る。
    """
    text = LISTING.read_text(encoding="utf-8")

    def section(name: str) -> str:
        marker = f"\n## {name}"
        if marker not in text:
            raise StoreError(f"listing-ja.md に「{name}」の節がありません")
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


def apply_listing(submission: dict, listing: dict[str, str]) -> dict:
    """日本語の掲載情報を差し替える。ほかの言語や価格には触らない。"""
    listings = submission.setdefault("listings", {})
    ja = listings.setdefault("ja", {}).setdefault("baseListing", {})
    ja["description"] = listing["description"]
    ja["shortDescription"] = listing["short"]
    ja["keywords"] = listing["keywords"]
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--msix", type=Path, help="差し替える MSIX。省略すると掲載情報だけ更新")
    parser.add_argument("--dry-run", action="store_true", help="送信せず、何を送るかだけ出す")
    args = parser.parse_args()

    listing = listing_from_markdown()
    print("掲載情報を読みました:")
    print(f"  説明 {len(listing['description'])} 文字 / "
          f"簡単な説明 {len(listing['short'])} 文字 / "
          f"キーワード {len(listing['keywords'])} 件")
    if "中村" not in listing["description"] or "金" not in listing["description"]:
        print("  ※ 説明に開発者名が入っていません", file=sys.stderr)

    if args.dry_run:
        print("\n--- 送る説明文 ---")
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

    print("前回の申請を複製しています…")
    submission = create_submission(token, store_id)
    submission = apply_listing(submission, listing)

    if args.msix:
        if not args.msix.is_file():
            print(f"MSIX が見つかりません: {args.msix}", file=sys.stderr)
            return 1
        work = Path(os.environ.get("TMPDIR", "/tmp")) / "archival-packager-store"
        work.mkdir(parents=True, exist_ok=True)
        bundle = stage_package(submission, args.msix, work)
        print(f"パッケージを送っています（{bundle.stat().st_size // 1024 // 1024} MB）…")
        upload(submission["fileUploadUrl"], bundle)

    print("申請を確定しています…")
    commit(token, store_id, submission)
    state = wait(token, store_id, submission["id"])
    print(f"結果: {state}")
    return 0 if state not in ("CommitFailed", "PreProcessingFailed",
                              "CertificationFailed") else 1


if __name__ == "__main__":
    sys.exit(main())
