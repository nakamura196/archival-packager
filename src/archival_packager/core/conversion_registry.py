"""正規化ルール表（Archivematica FPR の極小版）。

現行 Swift 実装の `Sources/AIP/ConversionRegistry.swift` に対応する。
PUID をキーに「保存用の派生物をどのツールでどう作るか」を引く。

## sips をやめて Pillow に統一した

Swift 版は画像→TIFF に macOS 内蔵の `sips` を使っていた。これは Windows に存在しない。

代替は Pillow（アプリ内で実行。外部プロセスを起こさない）。ImageMagick の同梱も
検討したが、macOS 向けの公式な再配布可能ビルドが無く、両OSで別ビルド＝別 libtiff に
なるため見送った。**同じ資料から作った保存用派生物のバイト列が OS によって変わると、
長期保存の観点で説明できなくなる**（どちらが「正」なのか決められない）。
判断の詳細は image_normalize.py の冒頭に書いてある。

PostScript/EPS→PDF の Ghostscript は引き続き外部プロセス。ただし**同梱しない**
（AGPL-3.0 であり、MIT のアプリに同梱すると配布ライセンスの判断が要る。加えて
macOS 向け公式ビルドが無い）。現行 Swift 版も同梱しておらず、PATH 上の gs を使う。
gs が無い環境では変換されず、原本がそのまま保存され report に警告が出る。
"""

from __future__ import annotations

from . import image_normalize
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

#: 出力フォーマットの名前（PRONOM の表記に合わせる）。
#: 名前を付けないと METS には "unknown" と入り、画面上は未識別に見える。
TIFF_NAME = "Tagged Image File Format"
PDF_NAME = "Acrobat PDF 1.7 - Portable Document Format"


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
            # アプリ内で実行するので args は使わない（image_normalize が判断する）。
            tool=image_normalize.TOOL,
            args=[],
            puid_out=TIFF_PUID,
            format_name_out=TIFF_NAME,
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
            format_name_out=PDF_NAME,
            out_extension="pdf",
        )

    return None
