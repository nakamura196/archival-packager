"""正規化ルールの引き当て口（Archivematica FPR の極小版）。

現行 Swift 実装の `Sources/AIP/ConversionRegistry.swift` に対応する。
PUID をキーに「保存用の派生物をどのツールでどう作るか」を引く。

## 規則そのものは rule_table.py に移した

以前はここに Python で直書きしていた。**利用者が規則を 1 件も足せなかった**ため、
Archivematica の FPR に倣って TOML の表に出した。組み込みの 2 規則も、利用者が
書くのとまったく同じ TOML で書いてある（rule_table.BUILTIN_TOML）。
組み込みが別の道を通ると、利用者向けの道は誰も使わないまま壊れるため。

このモジュールは、その表を 1 回だけ読んで覚えておく係。外から見た
`rule_for(puid, purpose)` の振る舞いは変えていない。

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

from pathlib import Path

from . import rule_table
from .aip_models import DerivativePurpose, NormalizationRule

TIFF_PUID = "fmt/353"
PDF_PUID = "fmt/276"  # PDF 1.7（PDF/A 化は将来 -dPDFA=2）

#: 規則の識別子。Archivematica が PREMIS の eventDetail に書く
#: `ArchivematicaFPRCommandID="a34ddc9b-..."` に相当する。
#:
#: **一度出した値は変えない。** 過去に作った AIP の PREMIS にはこの文字列が
#: 書き込まれている。改名すると、その AIP を後から読んだ人が「どの規則で
#: 作られたか」を今の表と突き合わせられなくなる。ルールの中身（使うツール、
#: 引数、出力形式）を差し替えても識別子は据え置く。
#:
#: UUID ではなく人が読める文字列にしたのは、METS を直接開いた人がその場で
#: 意味を取れるようにするため。識別子として要るのは一意性と不変性であって、
#: 乱数であることではない。
IMAGE_TO_TIFF_RULE = "image-to-tiff"
POSTSCRIPT_TO_PDF_RULE = "postscript-to-pdf"

#: 出力フォーマットの名前（PRONOM の表記に合わせる）。
#: 名前を付けないと METS には "unknown" と入り、画面上は未識別に見える。
TIFF_NAME = "Tagged Image File Format"
PDF_NAME = "Acrobat PDF 1.7 - Portable Document Format"

#: 読み込んだ表。**1 回の移管の途中で表が変わらないようにする。**
#: ファイルを毎回読み直すと、変換の途中で利用者が rules.toml を保存した場合に
#: 前半と後半で違う規則が効き、1 つの AIP の中で説明の付かない差が生まれる。
_table: rule_table.RuleTable | None = None


def table(user_path: Path | None = None) -> rule_table.RuleTable:
    """有効な規則表を返す（初回だけ読み、あとは覚えておく）。"""
    global _table
    if _table is None:
        _table = rule_table.load(user_path)
    return _table


def reload(user_path: Path | None = None) -> rule_table.RuleTable:
    """表を読み直す。利用者が rules.toml を書き換えたときと、テストで使う。"""
    global _table
    _table = rule_table.load(user_path)
    return _table


def rule_for(puid: str | None, purpose: DerivativePurpose) -> NormalizationRule | None:
    """PUID に対応する正規化ルールを返す。

    既に保存に適した形式（TIFF/PDF 等）や未知の PUID は None を返し、原本のまま保存する。
    """
    return table().rule_for(puid, purpose)


def warnings() -> list[str]:
    """規則表で読めなかったものの理由。**起動は妨げない。**

    表の不備は利用者にしか直せない。黙って捨てると、書いたはずの規則が
    効かない理由が誰にも分からなくなる。report に出して伝える。
    """
    return list(table().warnings)


def document() -> str:
    """AIP に同梱する、実際に効いていた表そのもの（TOML）。"""
    return table().document
