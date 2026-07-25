"""画像の保存用正規化（→ 非圧縮 TIFF）。

現行 Swift 実装は macOS 内蔵の `sips` を使っていた。Windows には無いので置き換えが要る。

## なぜ ImageMagick でなく Pillow か

当初は ImageMagick(`magick`) を両OSに同梱する方針だった。取りやめた理由:

1. **macOS 向けの公式な再配布可能ビルドが無い。** ImageMagick が配布しているのは
   Windows 向けの portable ビルドと Linux の AppImage、あとはソースだけ。
   macOS では Homebrew 版の実体と依存 dylib を集めて `install_name_tool` で
   参照を書き換える、という不安定な作業が必要になる。
2. **その結果、両OSで別ビルドになる。** Homebrew の版と Windows portable の版は
   一致せず、同梱する libtiff の版も揃わない。**同じ資料から作った保存用派生物の
   バイト列が OS によって変わる**ことになり、どちらが「正」なのか説明できない。

Pillow なら wheel が両OS向けに同一版で提供され、libtiff も wheel に同梱された
同じものが使われる。**同じ入力から同じバイト列が出る**方が、長期保存の説明として
はるかに素直なので、こちらを採る。

## 変換の方針

- **非圧縮**（`compression=None`）。可逆であることが自明な形にする。保存用の
  派生物に非可逆圧縮が既定で掛かると取り返しがつかない。
- **多フレーム画像は全フレームを残す**（`save_all=True`）。アニメーション GIF を
  そのまま保存すると Pillow は 1 フレーム目だけを書き、残りは警告も無く消える。
  「変換したのに中身が減っている」のは最悪の壊れ方なので、多ページ TIFF にする。
- **パレット＋透過は RGBA に上げる。** TIFF のパレットはアルファを持てないため、
  mode="P" のまま書くと透過情報だけが落ちる。
- **ICC プロファイルを持ち越す。** 無いと色の再現に必要な情報が失われる。
- 上記以外は**モードを変えない**（16bit グレースケールやパレットをそのまま保つ）。
  「とりあえず RGB に揃える」と階調やインデックスの情報が失われる。
"""

from __future__ import annotations

from pathlib import Path

import PIL
from PIL import Image, features

# ツール名（PREMIS の記録に使う）。
TOOL = "pillow"


def version_note() -> str:
    """PREMIS に記録するツール識別。libtiff の版まで出す。

    出力バイト列は Pillow と libtiff の版に依存する。あとから「この TIFF は
    何で作られたか」を追えるようにしておく。
    """
    libtiff = features.version("libtiff") or "unknown"
    return f"Pillow {PIL.__version__} (libtiff {libtiff})"


def to_tiff(src: Path, dest: Path) -> str:
    """`src` を非圧縮 TIFF として `dest` に書き、行った処理の説明を返す。

    戻り値は PREMIS の eventOutcomeDetailNote に入る。何をしたかが後から
    読んで分かる文字列にする（コマンドラインの代わり）。
    """
    with Image.open(src) as image:
        frames = getattr(image, "n_frames", 1)
        notes: list[str] = []

        save_kwargs: dict[str, object] = {"format": "TIFF", "compression": None}

        # 透過を持つパレット画像。TIFF のパレットはアルファを持てない。
        if image.mode == "P" and "transparency" in image.info:
            image = image.convert("RGBA")
            notes.append("透過を保持するため RGBA へ変換")

        if frames > 1:
            save_kwargs["save_all"] = True
            notes.append(f"{frames} フレームを多ページ TIFF として保持")

        if (icc := image.info.get("icc_profile")) is not None:
            save_kwargs["icc_profile"] = icc
            notes.append("ICC プロファイルを保持")

        dest.parent.mkdir(parents=True, exist_ok=True)
        image.save(dest, **save_kwargs)
        mode = image.mode

    detail = f"{version_note()}: {mode} → TIFF (非圧縮)"
    if notes:
        detail += " / " + " / ".join(notes)
    return detail
