#!/usr/bin/env python3
"""はじめての方向けガイド（docs/guide/）の、朗読つき動画と本文を作る。

画面が変わったら、これを走らせ直せば動画も撮り直せる。手で録った動画は、
画面を直すたびに古くなり、撮り直す人がいなくなる。

## 使い方

    op run --env-file=<ELEVENLABS_API_KEY の op:// 参照を書いたファイル> -- \\
      uv run --with flet-web==0.86.2 --with playwright==1.63.0 \\
        python scripts/docs/record_guide.py              # 日英両方
    ... python scripts/docs/record_guide.py --lang en    # 片方だけ
    ... python scripts/docs/record_guide.py --text-only  # ページの本文だけ書き直す

英語の声は macOS の say（Samantha）なので、`--lang en` だけなら op run は要らない。

初回だけ `uv run --with playwright==1.63.0 playwright install chromium` が要る。
ffmpeg も要る。

## 原稿は 1 か所

**朗読の原稿は `docs/guide/narration.json` にだけ書く。** 動画の声と、
ページの本文（`<!-- narration:sip -->` 〜 `<!-- /narration:sip -->` の間）は
どちらもここから作る。ページだけ直して声が古いまま、という食い違いを
構造的に起こさないため。

`say` は読み上げ専用の書き方。音声合成は訓読みの単漢字や英略語を
読み違えるので、仮名で書く（SIP → エスアイピー、開く → ひらく）。

## 仕組み

アプリを **ブラウザ版** として起動し（`guide_app.py`）、画面の見えない
ブラウザ（headless Chromium）で操作して録画する。録画中も手元の画面は
奪われない。

1 手順 = 原稿 1 行 = 操作 1 つ。手順の始まりで操作し、**その行の朗読が
読み終わるまで次の操作を待つ**。朗読の長さは合成してから測るので、
原稿を変えても声と画面はずれない。各手順の始まりの時刻を覚えておき、
あとで声をその時刻に置いて動画に重ねる。

ボタンは座標ではなく **名前で探す**。Flutter の読み上げ用の層
（`flt-semantics`）を有効にすると、ボタンや入力欄に名前が付く。名前は
日本語の原文で書き、英語版では `locales/en.py` の訳を引く。

合成した声は `~/.cache/archival-packager-guide/` に置き、原稿が同じなら
使い回す（ElevenLabs の文字数枠を撮り直しのたびに使わない）。

見えるカーソルは録画に映らないので、赤い丸を DOM で重ねて動かしている。

## 実物との違い

フォルダを選ぶ窓は出ない（`guide_app.py` の説明を参照）。朗読で補う。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from archival_packager.locales import en

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide"
OUT = GUIDE / "media"
NARRATION = GUIDE / "narration.json"
PAGES = {"ja": GUIDE / "index.md", "en": GUIDE / "en.md"}
SIZE = {"width": 1280, "height": 880}

#: 中村 覚の声（ElevenLabs の Professional Voice Clone）。日本語だけに使う。
VOICE_ID = "z52ElTqoKCvL4B34S5wD"
MODEL_ID = "eleven_v3"
#: 英語は macOS 標準の女性の声で読む（2026-09-23 のユーザー指定）。
#: API キーが要らないので、英語だけなら op run 無しで撮れる。
MAC_VOICE_EN = "Samantha"
#: Samantha が単語として読んでしまう略語。AIP は「エイプ」になる（whisper で確認）。
#: 声だけの読み替えなので、ページの本文には影響しない。
MAC_READINGS_EN = {"AIP": "A I P"}
CACHE = Path.home() / ".cache" / "archival-packager-guide"

#: 読み終わってから次の操作までの間（秒）。
GAP = 0.6

#: 動画に映るパスを短く、意味の分かる名前にするため、ホームの下に作る。
#: 録り終えたら消す。
WORK = Path.home() / "ap-guide-demo"

CURSOR_JS = """
() => {
  if (window.__mv) return;
  const c = document.createElement('div');
  c.style.cssText = 'position:fixed;z-index:99999;width:20px;height:20px;border-radius:50%;'
    + 'background:rgba(220,40,40,.55);border:2px solid #fff;pointer-events:none;'
    + 'left:-50px;top:-50px;transform:translate(-50%,-50%);transition:left .4s,top .4s';
  document.body.appendChild(c);
  window.__mv = (x, y) => { c.style.left = x + 'px'; c.style.top = y + 'px'; };
  window.__hide = () => { c.style.display = 'none'; };
}
"""


class Driver:
    """名前で探して、カーソルを動かしてから押す。"""

    def __init__(self, page: Page, lang: str) -> None:
        self.page = page
        self.lang = lang
        self.timeout = 120000

    def tr(self, text: str) -> str:
        return en.TEXTS.get(text, text) if self.lang == "en" else text

    def wait_ready(self) -> None:
        self.page.wait_for_selector("flt-semantics-placeholder", state="attached", timeout=30000)
        self.page.evaluate("document.querySelector('flt-semantics-placeholder')?.click()")
        self.page.wait_for_timeout(1000)
        self.page.evaluate(CURSOR_JS)

    def find(self, role: str | None, name: str, *, nth: int = 0, exact: bool = False):
        label = self.tr(name)
        if role:
            loc = self.page.get_by_role(role, name=label, exact=exact)
        else:  # 入力欄は flt-semantics ではなく <input> に名前が付く
            loc = self.page.get_by_role("textbox", name=label, exact=True)
        return loc.nth(nth)

    def click(self, role: str | None, name: str, *, nth: int = 0, exact: bool = False,
              wait: int = 400, dx: float = 0.5, dy: float = 0.5) -> None:
        loc = self.find(role, name, nth=nth, exact=exact)
        loc.wait_for(state="attached", timeout=self.timeout)
        box = loc.bounding_box()
        assert box, f"見つからない: {name}"
        x, y = box["x"] + box["width"] * dx, box["y"] + box["height"] * dy
        # 左端から少し入った所を押す（行全体が当たり判定の項目があるため）
        if box["width"] > 300 and dx == 0.5:
            x = box["x"] + 60
        self.page.evaluate(f"window.__mv({x},{y})")
        self.page.wait_for_timeout(500)
        self.page.mouse.click(x, y)
        self.page.wait_for_timeout(wait)

    def type(self, name: str, text: str) -> None:
        self.click(None, name, wait=200)
        self.page.keyboard.type(text, delay=60)

    def wait_for(self, role: str, name: str) -> None:
        self.find(role, name).wait_for(state="attached", timeout=300000)


# --------------------------------------------------------------------------
# 台本: 手順の id（narration.json と対応）と、その手順で行う操作
# --------------------------------------------------------------------------

Step = tuple[str, Callable[[Driver], None] | None]

SIP_RADIO = "SIP 作成（素材フォルダ／ZIP から受入パッケージ）"
AIP_RADIO = "AIP 作成（SIP から長期保存パッケージ）"


def _view(d: Driver) -> None:
    d.wait_for("button", "中身を見る")
    d.click("button", "中身を見る")


SCENES: dict[str, list[Step]] = {
    "sip": [
        ("intro", None),
        ("mode", lambda d: d.click("radio", SIP_RADIO)),
        ("input", lambda d: d.click("button", "フォルダを選ぶ", nth=0)),
        ("output", lambda d: d.click("button", "フォルダを選ぶ", nth=1)),
        # 記述メタデータはオプションを開く前に入れる。開くと欄が画面の下へ
        # 押し出され、読み上げ用の層から消えて探せなくなる。
        ("identifier", lambda d: d.type("識別子", "2026-transfer-01")),
        ("title", lambda d: d.type(
            "タイトル（必須）",
            "総務課 移管文書" if d.lang == "ja" else "Records transfer, General Affairs")),
        ("options", lambda d: d.click("button", "オプション")),
        ("virus", lambda d: d.click("checkbox", "ウイルス検査を行う（定義 DB が必要）", dx=0.1)),
        ("run", lambda d: d.click("button", "実行", exact=True)),
        ("done", lambda d: d.wait_for("button", "中身を見る")),
        ("view", lambda d: d.click("button", "中身を見る")),
        ("files", lambda d: d.click("tab", "ファイル", exact=True)),
    ],
    "aip": [
        ("intro", None),
        ("mode", lambda d: d.click("radio", AIP_RADIO)),
        # AIP 作成では入力のボタンが「SIP のフォルダを選ぶ」になる。名前で分けて押す
        # （「フォルダを選ぶ」の n 番目で数えると、日本語では部分一致で両方に当たり、
        # 英語では当たらず、言語でずれる）。
        ("input", lambda d: d.click("button", "SIP のフォルダを選ぶ", exact=True)),
        ("output", lambda d: d.click("button", "フォルダを選ぶ", exact=True)),
        ("run", lambda d: d.click("button", "実行", exact=True)),
        ("view", _view),
        ("workflow", lambda d: d.click("tab", "ワークフロー", exact=True)),
        ("stage", lambda d: d.click("button", "ウイルス検査")),
        ("normalization", lambda d: d.click("button", "保存用形式への変換")),
        ("fixity", lambda d: d.click("button", "完全性の確認")),
        ("events", lambda d: d.click("tab", "処理の記録", exact=True)),
    ],
}

#: 処理の完了を待つ手順。操作が終わってから読み始める（先に読むと、
#: 「終わると〜と出ます」が、まだ出ていない画面にかぶる）。
READ_AFTER = {("sip", "done"), ("aip", "view")}


# --------------------------------------------------------------------------
# 原稿と声
# --------------------------------------------------------------------------


def load_narration() -> dict:
    data = json.loads(NARRATION.read_text(encoding="utf-8"))
    for scene, steps in SCENES.items():
        ids = [s[0] for s in steps]
        have = [e["id"] for e in data[scene]]
        if ids != have:
            raise SystemExit(f"{scene}: 台本と原稿の手順が食い違う\n  台本: {ids}\n  原稿: {have}")
    return data


def speech(entry: dict, lang: str) -> str:
    line = entry[lang]
    return line.get("say") or line["text"]


def synthesize(text: str, lang: str) -> Path:
    """1 行を合成して音声ファイルを返す。同じ原稿なら前の結果を使う。"""
    if lang == "en":
        return synthesize_mac(text)
    key = hashlib.sha256(f"{VOICE_ID}|{MODEL_ID}|{text}".encode()).hexdigest()[:24]
    path = CACHE / f"{key}.mp3"
    if path.exists():
        return path
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY がない（op run 経由で起動する。使い方を参照）")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}?output_format=mp3_44100_128",
        data=json.dumps({
            "text": text,
            "model_id": MODEL_ID,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode(),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        body = res.read()
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def synthesize_mac(text: str) -> Path:
    """macOS の say で読む。"""
    for word, reading in MAC_READINGS_EN.items():
        text = re.sub(rf"\b{word}\b", reading, text)
    key = hashlib.sha256(f"say|{MAC_VOICE_EN}|{text}".encode()).hexdigest()[:24]
    path = CACHE / f"{key}.aiff"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["say", "-v", MAC_VOICE_EN, "-o", str(path), text], check=True)
    return path


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


# --------------------------------------------------------------------------
# ページの本文
# --------------------------------------------------------------------------


def update_pages(data: dict, langs: list[str]) -> None:
    """ページの `<!-- narration:<scene> -->` の間を、原稿の text で置き換える。"""
    for lang in langs:
        page = PAGES[lang]
        text = page.read_text(encoding="utf-8")
        for scene in SCENES:
            body = "\n\n".join(e[lang]["text"] for e in data[scene])
            pattern = re.compile(
                rf"(<!-- narration:{scene} -->\n).*?(<!-- /narration:{scene} -->)", re.S)
            if not pattern.search(text):
                raise SystemExit(f"{page.name}: narration:{scene} の印が無い")
            text = pattern.sub(lambda m, b=body: m.group(1) + b + "\n" + m.group(2), text)
        page.write_text(text, encoding="utf-8")
        print(f"  {page.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# 録画
# --------------------------------------------------------------------------


def prepare_material() -> Path:
    """動画に映す素材。原本は変更されないことを見せたいので、ふつうの名前にする。"""
    if WORK.exists():
        shutil.rmtree(WORK)
    src = WORK / "受入資料"
    (src / "文書").mkdir(parents=True)
    (WORK / "出力").mkdir()
    (src / "議事録.txt").write_text("令和8年度 第1回 課内会議 議事録\n", encoding="utf-8")
    (src / "文書" / "通知.txt").write_text("文書管理規程の改正について（通知）\n", encoding="utf-8")
    _sample_images(src)
    return src


def _sample_images(dest: Path) -> None:
    """保存用形式への変換を見せるための画像（PNG と JPEG）。"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 600), (236, 228, 212))
    draw = ImageDraw.Draw(img)
    for i in range(0, 800, 40):
        draw.line([(i, 0), (i, 600)], fill=(200, 190, 170))
    draw.rectangle([120, 100, 680, 500], outline=(90, 70, 50), width=6)
    img.save(dest / "図面.png")
    img.rotate(90, expand=True).save(dest / "写真.jpg", quality=90)


def start_app(lang: str, picks: list[Path], port: int) -> subprocess.Popen:
    env = dict(os.environ, GUIDE_LANG=lang, GUIDE_PORT=str(port),
               GUIDE_PICKS="|".join(str(p) for p in picks))
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("guide_app.py"))],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://localhost:{port}", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("アプリが起動しない")


def record(lang: str, scene: str, entries: list[dict], picks: list[Path],
           port: int = 8561) -> None:
    clips = [synthesize(speech(e, lang), lang) for e in entries]
    lengths = [duration(c) for c in clips]

    proc = start_app(lang, picks, port)
    tmp = Path(tempfile.mkdtemp(prefix="guide-video-"))
    starts: list[float] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport=SIZE, record_video_dir=str(tmp),
                                      record_video_size=SIZE, locale=lang)
            page = ctx.new_page()
            t0 = time.monotonic()
            page.goto(f"http://localhost:{port}")
            d = Driver(page, lang)
            d.wait_ready()
            page.wait_for_timeout(800)
            lead = time.monotonic() - t0  # 読み込み中の白い画面は切り落とす

            for (step_id, action), length in zip(SCENES[scene], lengths, strict=True):
                begin = time.monotonic()
                if action is not None:
                    action(d)
                    if (scene, step_id) in READ_AFTER:
                        begin = time.monotonic()
                starts.append(begin - t0 - lead)
                rest = begin + length + GAP - time.monotonic()
                if rest > 0:
                    page.wait_for_timeout(rest * 1000)
            page.wait_for_timeout(1000)

            (OUT / lang).mkdir(parents=True, exist_ok=True)
            # 写真は動画の poster に使うので、押した場所の赤い丸を消してから撮る
            page.evaluate("window.__hide()")
            page.screenshot(path=str(OUT / lang / f"{scene}.png"))
            ctx.close()
            browser.close()
        webm = next(tmp.glob("*.webm"))
        mux(webm, lead, clips, starts, OUT / lang / f"{scene}.mp4")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"  {lang}/{scene}.mp4")


def mux(webm: Path, lead: float, clips: list[Path], starts: list[float], out: Path) -> None:
    """声を各手順の始まりの時刻に置き、動画に重ねる。"""
    args = ["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{lead:.3f}", "-i", str(webm)]
    for c in clips:
        args += ["-i", str(c)]
    chains = [
        f"[{i + 1}:a]adelay=delays={max(0, int(s * 1000))}:all=1[a{i}]"
        for i, s in enumerate(starts)
    ]
    mix = "".join(f"[a{i}]" for i in range(len(clips)))
    graph = ";".join(chains) + f";{mix}amix=inputs={len(clips)}:normalize=0[aout]"
    args += [
        "-filter_complex", graph, "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24", "-preset", "slow",
        "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out),
    ]
    subprocess.run(args, check=True)


def record_home(lang: str, port: int = 8561) -> None:
    """起動直後の画面写真（動画にはしない）。"""
    proc = start_app(lang, [], port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport=SIZE, locale=lang)
            page.goto(f"http://localhost:{port}")
            Driver(page, lang).wait_ready()
            page.wait_for_timeout(1000)
            (OUT / lang).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(OUT / lang / "home.png"))
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=["ja", "en", "both"], default="both")
    ap.add_argument("--text-only", action="store_true", help="ページの本文だけ書き直す")
    ap.add_argument("--keep", action="store_true", help=f"{WORK} を残す（中身の確認用）")
    args = ap.parse_args()

    langs = ["ja", "en"] if args.lang == "both" else [args.lang]
    data = load_narration()
    update_pages(data, langs)
    if args.text_only:
        return 0

    try:
        for lang in langs:
            src = prepare_material()
            out = WORK / "出力"
            record_home(lang)
            record(lang, "sip", data["sip"], [src, out])
            sip = next(p for p in out.iterdir() if p.is_dir())
            aip_out = WORK / "出力-AIP"
            aip_out.mkdir()
            record(lang, "aip", data["aip"], [sip, aip_out])
    finally:
        if not args.keep:
            shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
