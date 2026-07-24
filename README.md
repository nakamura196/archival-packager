# Archival Packager

born-digital / デジタル化ファイルから **SIP（受入）** と **AIP（長期保存）** を作る
デスクトップアプリ。**macOS と Windows の両方**を 1 つのコードベースから出すことを目的とする。

## 現在の状態

**技術検証中。** 本実装はまだ始まっていない。`spike/` の 3 項目が通れば本実装へ進む。

| # | 検証項目 | 状態 |
|---|---|---|
| 1 | 同梱バイナリ（siegfried）の起動 | ✅ 合格 |
| 2 | Developer ID 署名と公証 | 署名 ✅ / 公証 実行中 |
| 3 | Windows ビルド（CI） | 未 |

検証の詳細・合否基準・実測で判明した落とし穴は [`spike/README.md`](spike/README.md) に記録している。

## 既存実装との関係

これは [`nakamura196/sipcreator-docker`](https://github.com/nakamura196/sipcreator-docker) の
`app/`（Swift / SwiftUI、macOS 専用、Developer ID 署名・公証済み）を、
Windows にも配布できるようにするための作り直しである。

**既存の Swift 実装は廃止しない。** 次の 2 つの役割で残す。

1. **本番** — 新実装が同等になるまでは、そちらが配布物である
2. **正解データの出力元** — 移植の検証は「同じ入力から両実装で生成した SIP/AIP が、
   タイムスタンプと UUID を除いて一致するか」で行う

この差分検証があるため、両者は独立したリポジトリとして保つ。

## なぜ Flet（Python）か

移植対象の中核は保存パッケージ生成であり、この領域の参照実装は Python に集中している。

| 用途 | Python | TypeScript |
|---|---|---|
| BagIt | `bagit`（米国議会図書館の公式実装） | 相当品なし |
| METS/PREMIS の XSD 検証 | `lxml` | 相当品なし |
| PDF テキスト抽出 | `pypdf` / `pdfminer.six` | 選択肢はあるが弱い |

BagIt を自前で書き直さずに済むことが決め手。保存パッケージの正しさがこのアプリの
存在理由なので、そこに実装リスクを持ち込みたくない。Archivematica も
CCA-Public/sipcreator も Python であり、今後の機能追加でも参照実装が手に入る。

代替案（Electron + TypeScript）は配布パイプラインが成熟している点で優れるが、
BagIt と XML 検証を自前で抱えることになる。`spike/` が失敗した場合はそちらへ切り替える。

## 開発

```
uv sync
uv run flet run spike
```

macOS でビルドする場合、`pod` が flutter と同じディレクトリにいると
`flet build` から見えなくなる既知の問題がある。詳細と回避策は
[`spike/README.md`](spike/README.md) の「検証中に判明した落とし穴」を参照。
