# Archival Packager

born-digital / デジタル化ファイルから **SIP（受入）** と **AIP（長期保存）** を作る
デスクトップアプリ。**macOS と Windows の両方**を 1 つのコードベースから出す。

## 現在の状態

技術検証（`spike/`）は 3 項目とも合格し、**コアの移植と差分検証まで完了**している。

| | 状態 |
|---|---|
| 同梱バイナリの起動 | ✅ 検証済み（macOS / Windows） |
| Developer ID 署名・公証 | ✅ 検証済み（`spctl: accepted / Notarized Developer ID`） |
| Windows ビルド | ✅ 検証済み（`windows-latest`） |
| コア移植 | ✅ 完了（270 テスト） |
| **現行実装との出力一致** | ✅ **差分なし** |

## 使い方（開発）

```
uv sync
uv run flet run .                 # 開発モードで起動
uv run pytest                     # テスト
```

配布物のビルド:

```
uv run flet build macos . --yes --product "Archival Packager"
./scripts/sign.zsh                # Developer ID + hardened runtime
```

同梱バイナリ（siegfried / ClamAV / Ghostscript / ImageMagick）は `binaries/<os>/` に置く。
リポジトリには含めない（サイズが大きく、取得は再現可能なため）。

## 移植の検証方法

**現行 Swift 実装との差分検証**が、この移植で最も効く検証手段。

```
uv run python scripts/differential_check.py --swift-app "/path/to/Archival Packager.app"
```

同じ入力を両実装に食わせ、生成時刻・UUID・`Bag-Software-Agent` を伏せて突き合わせる。
単体テストは「自分が想定した仕様」を固定するが、こちらは
**「現行実装が実際にやっていること」**と突き合わせる。実際にこの検証だけが見つけた
差異がある（`ByteCountFormatter` のロケール依存で `99 バイト` が `99 bytes` になっていた）。

## 既存実装との関係

これは [`nakamura196/archival-packager`](https://github.com/nakamura196/archival-packager) の
Swift 実装（macOS 専用、Developer ID 署名・公証済み）を、Windows にも配布できるように
作り直すもの。

**既存の Swift 実装は廃止しない。** 新実装が同等になるまではそちらが配布物であり、
かつ**差分検証の正解データを出す参照実装**でもある。

## なぜ Flet（Python）か

移植対象の中核は保存パッケージ生成であり、この領域の参照実装は Python に集中している。

| 用途 | 採用したもの | Swift 版 |
|---|---|---|
| BagIt | `bagit`（米国議会図書館の公式実装） | 自前実装 |
| METS/PREMIS | `lxml`（木として構築・XSD 検証可能） | 文字列連結 |
| CSV | 標準 `csv`（RFC 4180） | 自前実装 |
| ZIP | 標準 `zipfile` | `/usr/bin/zip`（Windows に無い） |
| PDF テキスト抽出 | `pypdf` | PDFKit（macOS 専用） |

自前実装をやめたことで、実際に仕様上の欠落が解消している。例えば BagIt は
ファイル名に LF/CR/% を含む場合マニフェスト中でのパーセントエンコードを要求するが、
Swift 版はこれを行っていない。

## 移植中に見つけた現行実装の問題

移植は現行実装の再読でもある。テストを書く過程で次が判明し、新実装では修正した。

1. **完全性確認が `failed` を `skipped` と報告する場合がある。**
   マニフェスト記載のファイルが全て存在しないとき、`checked == 0` の判定が先に効いて
   「マニフェストに有効な行がありません」を返す。ペイロードが消えている SIP を
   「検査せず飛ばした」と報告することになる。

2. **PREMIS の `eventIdentifierValue` を XML 生成のたびに振っていた。**
   同じ入力から 2 回生成すると別の出力になり、差分検証ができなかった。

3. **`structMap` の並び順が走査順に依存していた。** 同じ資料から違う METS が出うる。

4. **`accession.csv` の解析が引用フィールド内の改行を扱えない。**
   先に行で切ってからパースするため、原理的に扱えない。

5. **XML のエスケープ漏れの余地。** DFXML の `esc()` は `'` を扱っていない。

## 実装のドキュメント

- 技術検証と、そこで判明した落とし穴: [`spike/README.md`](spike/README.md)
- 各モジュールの設計判断はコード内のモジュール docstring に書いてある
  （「なぜそうしたか」を残す場所として、別ファイルよりコードの近くが良いと判断した）
