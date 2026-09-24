#!/usr/bin/env python3
"""画面マニュアル（docs/manual/）の画面写真を撮る。

画面の言葉や並びを変えたら、これを走らせ直す。写真が全部撮り直される。
手で撮った写真は、画面を直すたびに古くなり、古いことに誰も気づかない。

## 使い方

    uv run --with flet-web==0.86.2 --with playwright==1.63.0 \\
      python scripts/docs/capture_manual.py              # 日英両方
    ... python scripts/docs/capture_manual.py --lang en  # 片方だけ

声を使わないので、API キー（op run）は要らない。
初回だけ `uv run --with playwright==1.63.0 playwright install chromium` が要る。

`binaries/`（Siegfried と ClamAV）が無いと、ファイルが全部「未識別」になり、
ウイルス検査も「スキップ」になる。写真がそのまま出てしまうので、無ければ止める。
worktree で撮るときは、本体の `binaries/` へのリンクを置く。

## 仕組み

動画用の `record_guide.py` と同じ仕組みで動かす（アプリをブラウザ版で起動し、
見えないブラウザで操作する）。ボタンの探し方も、そちらの `Driver` を使う。

1 回の起動で、SIP を作り、中身を見て、続けて AIP を作り、中身を見るまでを
通して操作し、途中で写真を撮る。**写真ごとにアプリを起動し直さない。**
実際の利用者と同じ順番で画面が移るので、写真どうしの内容（パスや件数）が揃う。

説明している場所は、赤い枠で囲む。枠は DOM で重ねるだけで、アプリには
手を入れない。

写真の名前と順番は `SHOTS` にあり、マニュアルの本文はこの名前で画像を引く。
名前を変えるときは、`docs/manual/index.md` と `en.md` も直すこと。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

from playwright.sync_api import Locator, Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_guide import (  # noqa: E402  同じフォルダの部品を借りる
    AIP_RADIO,
    ROOT,
    SIP_RADIO,
    SIZE,
    Driver,
    _sample_images,
    start_app,
)

OUT = ROOT / "docs" / "manual" / "media"

#: 動画（record_guide.py の ~/ap-guide-demo）とは別の場所にする。どちらも起動時に
#: 作業フォルダを消すので、同じ場所だと同時に走らせたとき互いの素材を消し合う。
WORK = Path.home() / "ap-manual-demo"

#: 写真の名前。本文から参照される。撮れなかったものがあれば最後に知らせる。
SHOTS = [
    "home", "header", "mode", "input-output", "output-inside", "metadata",
    "options", "virus", "run-hint", "sip-result", "overview", "events-empty",
    "files", "raw", "continue-aip", "aip-options", "aip-result",
    "overview-aip", "events", "workflow-aip", "stage-virus", "stage-normalization", "stage-fixity",
    "files-aip", "full", "about", "about-license",
]

HIGHLIGHT_JS = """
([x, y, w, h]) => {
  const b = document.createElement('div');
  b.className = '__hl';
  b.style.cssText = 'position:fixed;z-index:99998;pointer-events:none;'
    + 'border:3px solid #d62828;border-radius:8px;box-shadow:0 0 0 2px rgba(255,255,255,.8);'
    + `left:${x}px;top:${y}px;width:${w}px;height:${h}px`;
  document.body.appendChild(b);
}
"""


#: 撮影用の素材の名前。英語の画面に日本語のフォルダ名が写らないよう、言語で分ける。
NAMES = {
    "ja": {"src": "受入資料", "sub": "文書", "out": "出力", "aip": "出力-AIP",
           "minutes": "議事録.txt", "notice": "通知.txt",
           "minutes_text": "令和8年度 第1回 課内会議 議事録\n連絡先: 03-1234-5678\n",
           "notice_text": "文書管理規程の改正について（通知）\n"},
    "en": {"src": "Accession", "sub": "Documents", "out": "Output", "aip": "Output-AIP",
           "minutes": "minutes.txt", "notice": "notice.txt",
           "minutes_text": "Minutes of the first section meeting, 2026\nContact: 03-1234-5678\n",
           "notice_text": "Notice: revision of the records management rules\n"},
}


def prepare_material(lang: str) -> dict[str, Path]:
    """写真に映す素材。

    動画用と同じ中身に、**個人情報の走査が拾う行を 1 つ足す**。結果の画面で
    「目視確認が必要な点」の囲みを見せるため（何も出ない画面だけでは、
    出たときに何をすればよいかを説明できない）。番号は架空のもの。
    """
    n = NAMES[lang]
    if WORK.exists():
        shutil.rmtree(WORK)
    paths = {k: WORK / n[k] for k in ("src", "out", "aip")}
    (paths["src"] / n["sub"]).mkdir(parents=True)
    paths["out"].mkdir()
    paths["aip"].mkdir()
    (paths["src"] / n["minutes"]).write_text(n["minutes_text"], encoding="utf-8")
    (paths["src"] / n["sub"] / n["notice"]).write_text(n["notice_text"], encoding="utf-8")
    _sample_images(paths["src"])
    if lang == "en":  # 動画用の部品は日本語の名前で作るので、付け替える
        (paths["src"] / "図面.png").rename(paths["src"] / "drawing.png")
        (paths["src"] / "写真.jpg").rename(paths["src"] / "photo.jpg")
    paths["sub"] = paths["src"] / n["sub"]
    return paths


class Camera:
    """赤い枠を付けて撮る。"""

    def __init__(self, page: Page, d: Driver, lang: str) -> None:
        self.page = page
        self.d = d
        self.dir = OUT / lang
        self.dir.mkdir(parents=True, exist_ok=True)
        self.taken: list[str] = []

    def box(self, locs: Iterable[Locator], grow: tuple[float, float, float, float] = (0, 0, 0, 0),
            pad: float = 6) -> list[float]:
        boxes = []
        for loc in locs:
            loc.wait_for(state="attached", timeout=self.d.timeout)
            b = loc.bounding_box()
            assert b, f"位置が取れない: {loc}"
            boxes.append(b)
        gl, gt, gr, gb = grow
        x0 = min(b["x"] for b in boxes) - pad - gl
        y0 = min(b["y"] for b in boxes) - pad - gt
        x1 = max(b["x"] + b["width"] for b in boxes) + pad + gr
        y1 = max(b["y"] + b["height"] for b in boxes) + pad + gb
        return [x0, y0, x1 - x0, y1 - y0]

    def redraw(self) -> None:
        """画面を描き直させる。

        ブラウザ版の Flutter は日本語のフォントを、要る字の分だけ後から取りに行く。
        届く前に描いた字は空白のまま残る（「素材フ　ルダ」「まだ　りません」）。
        窓の大きさを 1 px 変えて戻すと、届いたフォントで描き直す。
        実物のアプリは OS のフォントを使うので、この問題は写真の上だけのもの。
        """
        self.page.wait_for_timeout(700)
        self.page.set_viewport_size({"width": SIZE["width"] + 1, "height": SIZE["height"]})
        self.page.wait_for_timeout(300)
        self.page.set_viewport_size(SIZE)
        self.page.wait_for_timeout(700)

    def band(self, *locs: Locator, top: float = 0, bottom: float = 0) -> list[float]:
        """左の列の幅いっぱいに、locs の上下を囲む。見出しは探せないので top で広げる。"""
        _, y, _, hgt = self.box(locs)
        return [8, y - top, 420 - 16, hgt + top + bottom]

    def shot(self, name: str, *rects: list[float], clip: list[float] | None = None) -> None:
        """rects は赤い枠の並び（box / band で作る）。"""
        assert name in SHOTS, f"SHOTS に無い名前: {name}"
        self.page.evaluate("window.__mv && window.__mv(-50, -50)")  # 赤い丸は写真に要らない
        self.page.mouse.move(640, SIZE["height"] - 2)  # ツールチップを出さない
        self.redraw()
        for rect in rects:
            self.page.evaluate(HIGHLIGHT_JS, rect)
        self.page.wait_for_timeout(300)
        kw = {}
        if clip:
            kw["clip"] = dict(zip(("x", "y", "width", "height"), clip, strict=True))
        self.page.screenshot(path=str(self.dir / f"{name}.png"), **kw)
        self.page.evaluate("document.querySelectorAll('.__hl').forEach(e => e.remove())")
        self.taken.append(name)
        print(f"  {self.dir.name}/{name}.png")


def tip(d: Driver, label: str) -> Locator:
    """ツールチップの付いたボタン。名前はボタンではなく、外側の層に付く。"""
    return d.page.locator(f'flt-semantics[aria-label="{d.tr(label)}"]').first


def run(lang: str, port: int = 8571) -> list[str]:
    paths = prepare_material(lang)
    # フォルダ選択の窓は出ないので、押された順にこれを返す（guide_app.py）。
    # 2 番目は「出力先が資料のフォルダの中」の写真のためにわざと誤ったもの。
    picks = [paths["src"], paths["sub"], paths["out"], paths["aip"]]
    proc = start_app(lang, picks, port)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport=SIZE, locale=lang)
            page.goto(f"http://localhost:{port}")
            d = Driver(page, lang)
            d.timeout = 60000
            d.wait_ready()
            page.wait_for_timeout(800)
            cam = Camera(page, d, lang)
            story(d, cam, paths["out"])
            browser.close()
            return cam.taken
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def story(d: Driver, cam: Camera, out: Path) -> None:
    """利用者と同じ順に操作し、途中で撮る。

    見出しや説明文は読み上げ用の層に出てこない（探せない）ので、枠は
    近くのボタンや入力欄を基準にして、見出しの分だけ上下に広げて描く。
    """
    page = d.page
    w, h = SIZE["width"], SIZE["height"]
    left_col = [0, 0, 440, h]

    band, box = cam.band, cam.box

    def blur() -> None:
        """入力中の欄の強調を外す。右側の空いた所を押す。

        **左の列の欄を押して外さないこと。** 画面の下に隠れた欄の位置には
        「実行」が重なっていて、押すと実行されてしまう（実際に SIP が 2 つできた）。
        """
        page.mouse.click(900, 820)
        page.wait_for_timeout(300)

    def scroll_left(dy: int) -> None:
        page.mouse.move(200, 500)
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(600)

    # ---- 起動した画面 -------------------------------------------------
    cam.shot("home")
    cam.shot("header", box([d.find("button", "表示言語")]),
             box([tip(d, "使い方・ライセンス・連絡先")]), clip=[0, 0, w, 60])

    # ---- 何を作るか ---------------------------------------------------
    cam.shot("mode", band(d.find("radio", SIP_RADIO), d.find("radio", "素材から AIP まで一気通貫"),
                          top=26, bottom=34), clip=left_col)

    # ---- 入力と出力先 -------------------------------------------------
    cam.shot("run-hint", band(d.find("button", "実行", exact=True), bottom=20), clip=left_col)
    d.click("button", "フォルダを選ぶ", nth=0)
    d.click("button", "フォルダを選ぶ", nth=1)  # わざと資料の中を選ぶ
    page.wait_for_timeout(500)
    cam.shot("output-inside", band(d.find("button", "フォルダを選ぶ", nth=1), top=26, bottom=56),
             clip=left_col)
    d.click("button", "フォルダを選ぶ", nth=1)  # 選び直す
    page.wait_for_timeout(500)
    cam.shot("input-output", band(d.find("button", "フォルダを選ぶ", nth=0),
                                  d.find("button", "フォルダを選ぶ", nth=1), top=26, bottom=22),
             clip=left_col)

    # ---- 記述メタデータ ----------------------------------------------
    # オプションを開く前に入れる（開くと欄が下へ押し出され、探せなくなる）。
    d.type("識別子", "2026-transfer-01")
    d.type("タイトル（必須）",
           "総務課 移管文書" if d.lang == "ja" else "Records transfer, General Affairs")
    d.type("年代", "2024–2025")
    blur()
    cam.shot("metadata", band(d.find(None, "識別子"), d.find(None, "内容・範囲"), top=26),
             clip=left_col)

    # ---- オプション ---------------------------------------------------
    d.click("button", "オプション")
    d.click("checkbox", "個人情報(PII)を走査する", dx=0.1)
    page.wait_for_timeout(300)
    cam.shot("options", band(d.find("button", "オプション"),
                             d.find("checkbox", "成果物を ZIP（無圧縮）に固める")),
             clip=left_col)
    d.click("checkbox", "ウイルス検査を行う（定義 DB が必要）", dx=0.1)
    page.wait_for_timeout(300)
    scroll_left(400)  # 定義の欄は「実行」の陰になるので、左の列を送る
    cam.shot("virus", band(d.find("button", "定義を取得 / 更新"), top=44), clip=left_col)
    scroll_left(-2000)

    # ---- 実行と結果 ---------------------------------------------------
    d.click("button", "実行", exact=True)
    d.wait_for("button", "中身を見る")
    page.wait_for_timeout(1200)
    made = [p.name for p in out.iterdir()]
    # 操作の誤りで余計に実行されていないか（隠れた欄を押して「実行」に当たったことがある）
    assert len(made) == 1, f"SIP が 1 つではない: {made}"
    cam.shot("sip-result", box([d.find("button", "中身を見る"), d.find("button", "場所を開く"),
                                d.find("button", "この SIP から AIP を作る")], grow=(0, 30, 0, 0)))

    # ---- 中身を見る（SIP） --------------------------------------------
    d.click("button", "中身を見る")
    page.wait_for_timeout(800)
    cam.shot("overview")
    # SIP の段階では処理の記録は空（「まだありません」と出る）。その画面も 1 枚撮る。
    for tab, name in (("処理の記録", "events-empty"), ("ファイル", "files"), ("生データ", "raw")):
        d.click("tab", tab, exact=True)
        page.wait_for_timeout(700)
        cam.shot(name)
    d.click("button", "閉じる")
    page.wait_for_timeout(600)

    # ---- AIP へ進む ---------------------------------------------------
    d.click("button", "この SIP から AIP を作る")
    page.wait_for_timeout(600)
    cam.shot("continue-aip", band(d.find("radio", AIP_RADIO)),
             band(d.find("button", "SIP のフォルダを選ぶ", exact=True), top=26, bottom=22),
             clip=left_col)
    d.click("button", "フォルダを選ぶ", exact=True)  # 出力先を AIP 用に
    # オプションは SIP のときに開いたまま残っている。閉じていれば開く。
    if not d.find("checkbox", "成果物を ZIP（無圧縮）に固める").count():
        d.click("button", "オプション")
    page.wait_for_timeout(400)
    cam.shot("aip-options", band(d.find("button", "オプション"),
                                 d.find("checkbox", "成果物を ZIP（無圧縮）に固める")),
             clip=left_col)
    d.click("button", "実行", exact=True)
    d.wait_for("button", "中身を見る")
    page.wait_for_timeout(1200)
    r = box([d.find("button", "中身を見る"), d.find("button", "開く", exact=True)],
            grow=(0, 30, 0, 26))
    r[2] += r[0] - 450  # 左端を右の列の端に揃える（見出しとパスも囲む）
    r[0] = 450
    cam.shot("aip-result", r)

    # ---- 中身を見る（AIP）とワークフロー ------------------------------
    d.click("button", "中身を見る")
    page.wait_for_timeout(800)
    cam.shot("overview-aip")
    d.click("tab", "処理の記録", exact=True)
    page.wait_for_timeout(700)
    cam.shot("events")
    d.click("tab", "ワークフロー", exact=True)
    page.wait_for_timeout(700)
    cam.shot("workflow-aip")
    for stage, name in (("ウイルス検査", "stage-virus"),
                        ("保存用形式への変換", "stage-normalization"),
                        ("完全性の確認", "stage-fixity")):
        d.click("button", stage)
        page.wait_for_timeout(500)
        cam.shot(name, box([d.find("button", stage)]))
    d.click("tab", "ファイル", exact=True)
    page.wait_for_timeout(700)
    cam.shot("files-aip")
    d.click("button", "閉じる")
    page.wait_for_timeout(600)

    # ---- 一気通貫 -----------------------------------------------------
    d.click("radio", "素材から AIP まで一気通貫")
    page.wait_for_timeout(400)
    cam.shot("full", band(d.find("radio", "素材から AIP まで一気通貫"), bottom=34), clip=left_col)
    d.click("radio", SIP_RADIO)

    # ---- このアプリについて -------------------------------------------
    tip(d, "使い方・ライセンス・連絡先").click()
    page.wait_for_timeout(800)
    cam.shot("about")
    d.click("tab", "ライセンス", exact=True)
    page.wait_for_timeout(700)
    cam.shot("about-license")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lang", choices=["ja", "en", "both"], default="both")
    ap.add_argument("--keep", action="store_true", help=f"{WORK} を残す（中身の確認用）")
    args = ap.parse_args()

    if not (ROOT / "binaries").exists():
        raise SystemExit("binaries/ が無い。ファイルが全部「未識別」で写ってしまう（使い方を参照）")

    langs = ["ja", "en"] if args.lang == "both" else [args.lang]
    try:
        for lang in langs:
            taken = run(lang)
            missing = [s for s in SHOTS if s not in taken]
            if missing:
                raise SystemExit(f"{lang}: 撮れなかった写真がある: {missing}")
    finally:
        if not args.keep:
            shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
