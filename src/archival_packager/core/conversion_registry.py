"""正規化ルール表（Archivematica FPR の極小版）。

現行 Swift 実装の `Sources/AIP/ConversionRegistry.swift` に対応する。
PUID をキーに「保存用の派生物をどのツールでどう作るか」を引く。

## sips をやめて ImageMagick に統一した

Swift 版は画像→TIFF に macOS 内蔵の `sips` を使っていた。これは Windows に存在しない。

代替として ImageMagick(`magick`) を**両OSで**同梱する。Windows 側だけ別ツールにする
選択肢もあるが、採らない。**同じ資料から作った保存用派生物のバイト列が OS によって
変わると、長期保存の観点で説明できなくなる**（どちらが「正」なのか決められない）。
変換ツールとそのバージョンは PREMIS event に記録するので、片方だけ差し替えると
記録の意味も揃わなくなる。

PostScript/EPS→PDF の Ghostscript は元から両OS対応なので変更しない。
"""

from __future__ import annotations

from .aip_models import DerivativePurpose, NormalizationRule

# ImageMagick で TIFF 化する画像フォーマット。
_IMAGE_TO_TIFF: frozenset[str] = frozenset(
    {
        "fmt/11", "fmt/12", "fmt/13", "fmt/935",            # PNG
        "fmt/41", "fmt/42", "fmt/43", "fmt/44",             # JPEG
        "x-fmt/398", "x-fmt/390", "x-fmt/391",              # JPEG (旧)
        "fmt/3", "fmt/4",                                   # GIF
        "fmt/116", "fmt/117", "fmt/119", "x-fmt/270",       # BMP
    }
)

# Ghostscript で PDF 化する PostScript/EPS フォーマット。
_POSTSCRIPT_TO_PDF: frozenset[str] = frozenset(
    {
        "fmt/124", "fmt/501",                               # PostScript
        "x-fmt/91", "x-fmt/406", "x-fmt/407", "x-fmt/408",  # PostScript (旧)
        "fmt/122", "fmt/123",                               # EPS
    }
)

TIFF_PUID = "fmt/353"
PDF_PUID = "fmt/276"  # PDF 1.7（PDF/A 化は将来 -dPDFA=2）


def rule_for(puid: str | None, purpose: DerivativePurpose) -> NormalizationRule | None:
    """PUID に対応する正規化ルールを返す。

    既に保存に適した形式（TIFF/PDF 等）や未知の PUID は None を返し、原本のまま保存する。
    """
    if purpose is not DerivativePurpose.PRESERVATION or not puid:
        return None

    if puid in _IMAGE_TO_TIFF:
        return NormalizationRule(
            puid_in=puid,
            purpose=DerivativePurpose.PRESERVATION,
            tool="magick",
            # -compress none: 可逆であることを明示する。保存用途で
            # 非可逆圧縮が既定で掛かると取り返しがつかない。
            args=["{in}", "-compress", "none", "{out}"],
            puid_out=TIFF_PUID,
            out_extension="tiff",
        )

    if puid in _POSTSCRIPT_TO_PDF:
        return NormalizationRule(
            puid_in=puid,
            purpose=DerivativePurpose.PRESERVATION,
            tool="gs",
            args=[
                "-dNOPAUSE",
                "-dBATCH",
                "-dSAFER",
                "-sDEVICE=pdfwrite",
                "-sOutputFile={out}",
                "{in}",
            ],
            puid_out=PDF_PUID,
            out_extension="pdf",
        )

    return None
