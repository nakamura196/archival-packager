"""共有用の画像（OGP、1200×630）を日英 1 枚ずつ作る。

出力: docs/assets/ogp/ogp-ja.png・ogp-en.png（_config.yml と英語ページの image が指している）
元にする画面写真: docs/images/main-{ja,en}.png。写真を撮り直したら、これも走らせ直す。
使い方: python3 scripts/docs/make_ogp.py（リポジトリの直下で。Pillow とヒラギノ角ゴシックが要る）
"""
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W, H = 1200, 630
B = "/System/Library/Fonts/ヒラギノ角ゴシック W{}.ttc"
TEXT = {
  "ja": ("デジタル資料から、", "OAIS の情報パッケージ", "（SIP / AIP）を作るアプリ", "コマンドを打たずに使えます"),
  "en": ("Build OAIS information", "packages (SIP / AIP)", "from your files", "No command line needed"),
}
for lang, lines in TEXT.items():
    im = Image.new("RGB", (W, H), "#f5f7fb")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 10], fill="#2563eb")
    shot = Image.open(f"docs/images/main-{lang}.png").convert("RGB")
    sw = 500; sh = int(shot.height * sw / shot.width)
    shot = shot.resize((sw, sh), Image.LANCZOS)
    x, y = W - sw - 40, 70
    sh_crop = min(sh, H - y + 40)
    shadow = Image.new("RGBA", (sw + 40, sh_crop + 40), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([20, 20, sw + 20, sh_crop + 20], 14, fill=(15, 23, 42, 60))
    im.paste(shadow.filter(ImageFilter.GaussianBlur(12)), (x - 20, y - 14), shadow.filter(ImageFilter.GaussianBlur(12)))
    mask = Image.new("L", (sw, sh_crop), 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, sw, sh_crop + 30], 14, fill=255)
    im.paste(shot.crop((0, 0, sw, sh_crop)), (x, y), mask)
    d.text((64, 88), "macOS · Windows", font=ImageFont.truetype(B.format(6), 26), fill="#2563eb")
    d.text((64, 128), "Archival Packager", font=ImageFont.truetype(B.format(8), 52), fill="#0f172a")
    f = ImageFont.truetype(B.format(6), 34 if lang == "ja" else 36)
    for i, t in enumerate(lines[:3]):
        d.text((64, 250 + i * 56), t, font=f, fill="#1e293b")
    d.text((64, 450), lines[3], font=ImageFont.truetype(B.format(3), 26), fill="#475569")
    im.save(f"docs/assets/ogp/ogp-{lang}.png", optimize=True)
    print(lang, im.size)
