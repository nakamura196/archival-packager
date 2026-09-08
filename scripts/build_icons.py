#!/usr/bin/env python3
"""アプリのアイコンを描いて、必要な形に書き出す。

描画そのものは Swift 版（nakamura196/archival-packager）の
scripts/build_icon.py から持ってきた。**同じ見た目にするため。**
macOS 版と Windows 版でアイコンが違うと、同じアプリだと分からなくなる。

Pillow だけで描く。外部の画像素材もネットワークも要らないので、
いつ実行しても同じものが出る。

書き出し先:
    assets/icon.png                     Flet がアプリのアイコンに使う（1024px）
    packaging/windows/Assets/*.png      MSIX（Microsoft Store）が使う

使い方:
    uv run python scripts/build_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# 以下、色と寸法は Swift 版と同じ値。変えると両OSで見た目がずれる。
MASTER = 1024

BG_TOP = (28, 110, 140)      # deep teal-blue
BG_BOTTOM = (16, 64, 92)     # darker blue
BOX_BODY = (245, 247, 248)   # near-white box body
BOX_LID = (214, 224, 228)    # cool light grey lid
BOX_LINE = (120, 140, 150)   # subtle outline / detail
LABEL = (255, 200, 92)       # warm amber label tab
CHECK = (28, 110, 140)       # checkmark in bg color, sits on the label


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    """Build a vertical gradient image (RGB)."""
    grad = Image.new("RGB", (1, size))
    px = grad.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px[0, y] = (
            _lerp(top[0], bottom[0], t),
            _lerp(top[1], bottom[1], t),
            _lerp(top[2], bottom[2], t),
        )
    return grad.resize((size, size))


def _rounded_mask(size: int, radius: int) -> Image.Image:
    """Alpha mask for a full-bleed rounded-rect canvas."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def render_master() -> Image.Image:
    """Render the icon at MASTER resolution as RGBA."""
    S = MASTER
    # Supersample for clean anti-aliasing of the glyph.
    SS = 2
    W = S * SS

    # --- background: rounded-rect with vertical gradient ---
    radius = int(W * 0.22)  # Big Sur-ish rounding on the full-bleed canvas
    grad = _vertical_gradient(W, BG_TOP, BG_BOTTOM).convert("RGBA")
    mask = _rounded_mask(W, radius)
    canvas = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    canvas.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(canvas)

    # --- archive box glyph, centered ---
    # Layout in master coords. Box occupies the central area.
    cx = W / 2
    box_w = W * 0.58
    box_h = W * 0.40
    # Lid sits above the body.
    lid_h = W * 0.115
    gap = W * 0.012  # small gap between lid and body

    body_top = W * 0.46
    body_bottom = body_top + box_h
    body_left = cx - box_w / 2
    body_right = cx + box_w / 2

    lid_bottom = body_top - gap
    lid_top = lid_bottom - lid_h
    lid_overhang = W * 0.025
    lid_left = body_left - lid_overhang
    lid_right = body_right + lid_overhang

    r_body = W * 0.035
    r_lid = W * 0.030
    line_w = max(2, int(W * 0.006))

    # subtle drop shadow under the whole glyph for depth
    shadow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle(
        [body_left, body_top, body_right, body_bottom],
        radius=r_body, fill=(0, 0, 0, 70),
    )
    sd.rounded_rectangle(
        [lid_left, lid_top, lid_right, lid_bottom],
        radius=r_lid, fill=(0, 0, 0, 70),
    )
    shadow = shadow.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(W * 0.012))
    # offset shadow slightly down
    off = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    off.paste(shadow, (0, int(W * 0.012)))
    canvas = Image.alpha_composite(canvas, off)
    d = ImageDraw.Draw(canvas)

    # box body
    d.rounded_rectangle(
        [body_left, body_top, body_right, body_bottom],
        radius=r_body, fill=BOX_BODY,
    )
    # lid
    d.rounded_rectangle(
        [lid_left, lid_top, lid_right, lid_bottom],
        radius=r_lid, fill=BOX_LID,
    )
    # seam line just under the lid on the body
    d.line(
        [(body_left + r_body, body_top + W * 0.0),
         (body_right - r_body, body_top + W * 0.0)],
        fill=BOX_LINE, width=line_w,
    )

    # center vertical handle slot on the body (archive box detail)
    slot_w = W * 0.14
    slot_h = W * 0.045
    slot_top = body_top + box_h * 0.30
    d.rounded_rectangle(
        [cx - slot_w / 2, slot_top, cx + slot_w / 2, slot_top + slot_h],
        radius=slot_h / 2, fill=BOX_LINE,
    )

    # --- label tab with checkmark (the "verified package" accent) ---
    label_w = W * 0.255
    label_h = W * 0.185
    label_cx = cx
    label_top = body_top + box_h * 0.50
    label_left = label_cx - label_w / 2
    label_right = label_cx + label_w / 2
    label_bottom = label_top + label_h
    r_label = W * 0.022
    d.rounded_rectangle(
        [label_left, label_top, label_right, label_bottom],
        radius=r_label, fill=LABEL,
    )

    # checkmark inside the label
    lcx = label_cx
    lcy = (label_top + label_bottom) / 2
    cw = label_w * 0.62
    chk_w = max(3, int(W * 0.016))
    p1 = (lcx - cw * 0.40, lcy + cw * 0.02)
    p2 = (lcx - cw * 0.08, lcy + cw * 0.26)
    p3 = (lcx + cw * 0.44, lcy - cw * 0.28)
    d.line([p1, p2, p3], fill=CHECK, width=chk_w, joint="curve")
    # round caps
    rcap = chk_w / 2
    for (px_, py_) in (p1, p3):
        d.ellipse([px_ - rcap, py_ - rcap, px_ + rcap, py_ + rcap], fill=CHECK)

    # downsample to master size
    out = canvas.resize((S, S), Image.LANCZOS)
    return out

def emit() -> None:
    master = render_master()  # 1024px

    # Flet 用。ビルド時にこれから各サイズが作られる。
    app_icon = ROOT / "assets" / "icon.png"
    app_icon.parent.mkdir(parents=True, exist_ok=True)
    master.save(app_icon)
    print(f"{app_icon.relative_to(ROOT)}  1024x1024")

    # MSIX 用。サイズは AppxManifest.xml.in が参照しているものと揃える。
    msix = ROOT / "packaging" / "windows" / "Assets"
    msix.mkdir(parents=True, exist_ok=True)
    for name, size in [
        ("Square44x44Logo", 44),
        ("Square150x150Logo", 150),
        ("Square310x310Logo", 310),
        ("StoreLogo", 50),
    ]:
        path = msix / f"{name}.png"
        master.resize((size, size), Image.LANCZOS).save(path)
        print(f"{path.relative_to(ROOT)}  {size}x{size}")

    # ワイドタイルだけ正方形ではないので、背景の上に中央寄せで置く。
    wide = Image.new("RGBA", (310, 150), (0, 0, 0, 0))
    glyph = master.resize((132, 132), Image.LANCZOS)
    wide.paste(glyph, ((310 - 132) // 2, (150 - 132) // 2), glyph)
    path = msix / "Wide310x150Logo.png"
    wide.save(path)
    print(f"{path.relative_to(ROOT)}  310x150")


if __name__ == "__main__":
    emit()
