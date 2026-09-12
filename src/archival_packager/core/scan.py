"""入力ツリーの走査と、ファイルから読める事実の採取。

退役した Swift 実装の `Sources/SIP/Orchestrator.swift` の `scan` に由来する。

画像の技術的特性（`image_characteristics`）もここに置いてある。原本を開いて
事実を読むだけ、という点で走査と同じ性質の処理だから。**書き込む側とは分けておく**
（image_normalize.py は原本から派生物を作る側で、あちらは出力を持つ）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageMode

from .models import ImageCharacteristics, ScannedFile, SIPPipelineError

# 走査から除外するファイル名。資料ではなく OS が勝手に作るもの。
_EXCLUDED_NAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def scan(root: Path) -> list[ScannedFile]:
    """root 配下の通常ファイルを列挙する。

    シンボリックリンクは辿らない。辿ると (a) 入力ツリーの外にあるファイルを
    受入対象に含めてしまい、(b) 循環リンクで無限走査になる。
    """
    if not root.is_dir():
        raise SIPPipelineError.io(f"入力フォルダを走査できません: {root}")

    # root 自体は解決しておく。macOS では /var が /private/var への
    # シンボリックリンクであり、これを揃えないと相対パス化が崩れる
    # （zip 展開先の一時ディレクトリで実際に問題になった）。
    resolved_root = root.resolve()

    out: list[ScannedFile] = []
    for path in resolved_root.rglob("*"):
        if path.name in _EXCLUDED_NAMES:
            continue
        if path.is_symlink() or not path.is_file():
            continue

        stat = path.stat()
        out.append(
            ScannedFile(
                relative_path=path.relative_to(resolved_root).as_posix(),
                absolute_path=path,
                size_bytes=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )
        )

    # 並び順を固定する。走査順に任せると同じ入力から違うマニフェスト・METS が出る。
    out.sort(key=lambda f: f.relative_path)
    return out


def has_objects_dir(root: Path) -> bool:
    """入力直下に objects/ があるか。

    あれば「事前確定モード」（その構造を尊重し objects/ の中身だけを payload にする）、
    無ければ「自動ラップモード」（中身を objects/ に写す）。
    Archivematica の transfer 構造をそのまま受け取れるようにするための判定。
    """
    return (root / "objects").is_dir()


# --------------------------------------------------------------------------
# 画像の技術的特性
# --------------------------------------------------------------------------

#: 白黒 2 値（Pillow の mode "1"）のビット深度。
#:
#: Pillow は内部で 1 画素 1 バイトに展開して持つので、型情報を見ると 8 ビットに見える。
#: **記録したいのはメモリ上の持ち方ではなく画像そのものの性質**なので、
#: ここだけは型から導かずに 1 と書く。
_BILEVEL_BITS = 1


def image_characteristics(path: Path) -> ImageCharacteristics:
    """画像 1 点の技術的特性を読む。**原本は開くだけで、書き換えない。**

    画素データは復号しない。欲しい値はすべてヘッダにあり、`Image.open` は
    ヘッダしか読まないので、数万点の移管でも走査時間がほとんど増えない。
    その代わり **ヘッダは正しいが画素データが途中で切れている画像は、ここでは
    分からない**。ここで分かるのは「開けたかどうか」までだと承知して読むこと。
    """
    try:
        with Image.open(path) as image:
            width, height = image.size
            x_dpi, y_dpi = _dpi(image)
            return ImageCharacteristics(
                width=width,
                height=height,
                color_space=image.mode,
                bits_per_sample=_bits_per_sample(image.mode),
                x_dpi=x_dpi,
                y_dpi=y_dpi,
            )
    except Exception as exc:
        # **種類を問わず捕まえる。** Pillow は入力次第で違う例外を投げる
        # （UnidentifiedImageError / OSError / ValueError / SyntaxError /
        # DecompressionBombError …）。壊れた画像は資料の中に普通に混ざっていて、
        # そこで移管全体を止めるのは割に合わない。
        # ただし**握りつぶさない**。読めなかったという事実を持ち帰り、DFXML と
        # 警告の両方に残す（このリポジトリで最も避けたいのは沈黙する失敗）。
        return ImageCharacteristics(error=_safe_detail(exc, path))


def _bits_per_sample(mode: str) -> int | None:
    """1 サンプルあたりのビット数。分からなければ None。

    Pillow が mode ごとに持っている型情報から導く。自前の対応表を作ると、
    Pillow が mode を足したときに黙って古い値を返し続けるため。
    """
    if mode == "1":
        return _BILEVEL_BITS
    try:
        typestr = ImageMode.getmode(mode).typestr  # 例: "|u1" "<u2" "<f4"
    except (KeyError, ValueError):
        return None
    digits = typestr[2:]
    return int(digits) * 8 if digits.isdigit() else None


def _dpi(image: Image.Image) -> tuple[float | None, float | None]:
    """解像度（dpi）。持っていない画像のほうが多いので、無ければ None を返す。

    **推測で埋めない。** 「画面表示なら 72 dpi だろう」と補うと、原本が解像度を
    持っていなかったという事実そのものが記録から消える。
    """
    dpi = image.info.get("dpi")
    if not isinstance(dpi, tuple) or len(dpi) != 2:
        return None, None
    try:
        x, y = float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError, ZeroDivisionError):
        # TIFF の解像度は分数（IFDRational）で入っており、分母が 0 のものが実在する。
        return None, None
    # 0 や負の dpi を書いた画像がある。値として意味を持たないので記録しない。
    return (x if x > 0 else None), (y if y > 0 else None)


def _safe_detail(exc: Exception, path: Path) -> str:
    """例外の説明から、利用者名の入りうる絶対パスを落とす。

    Pillow の例外文にはファイルの絶対パスがそのまま入る
    （"cannot identify image file '/Users/<名前>/…'"）。この文字列は DFXML に入り、
    パッケージは外部に渡りうる。**dfxml.build が image_filename に絶対パスを
    既定で書かないのと同じ理由**で、ここでも落とす。ファイル名は残す
    （どのファイルの話か分からない記録は、読んでも何もできない）。

    親ディレクトリを消すのは、絶対パスのときだけにする。相対パスだと親が "."
    になることがあり、文中のピリオドを全部置き換えて文章を壊す
    （実際にそうなった: "cannot identify image file 'sig…png'"）。
    """
    text = f"{type(exc).__name__}: {exc}".replace(str(path), path.name)
    parent = str(path.parent)
    if path.is_absolute() and len(parent) > 1:
        text = text.replace(parent, "…")
    return text
