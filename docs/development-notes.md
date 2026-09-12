# 開発の記録（移植のいきさつ）

このアプリは、macOS 専用の Swift 実装（`nakamura196/archival-packager-swift`）を
Flet / Python へ移植したもの。**移植の途中経過は README から外して、ここに移した。**
公開リポジトリの入口に進捗表や「次にやること」が並んでいても、読む人の役に立たないため。

記録として残すのは、当時の判断の理由が後から辿れなくなると、同じ検討を繰り返すことになるから。

---

## 現在の状態

移植は完了し、署名済み `.app` まで通っている。

| | 状態 |
|---|---|
| コア移植（SIP / AIP 全モジュール） | ✅ 完了（450 テスト） |
| **現行実装との出力一致** | ✅ **差分なし** |
| UI（3 モード） | ✅ パッケージ版で起動確認済み |
| Developer ID 署名 | ✅ 未署名 Mach-O ゼロ |
| **公証（Apple）** | ✅ **Accepted / `spctl: accepted`**（ClamAV 同梱後・357MB） |
| フォーマット識別（siegfried） | ✅ 同梱・起動確認済み |
| ウイルス検査（ClamAV） | ✅ 同梱・自作シグネチャで検出まで確認 |
| 画像 → TIFF（Pillow） | ✅ アプリ内変換・通しで確認 |
| Windows ビルド | ✅ 本実装で通過（2026-09-08 の CI。同梱ツールの起動確認まで） |
| Windows のコード署名 | ✅ Microsoft ストア経由で解決（ストア側が署名する） |
| ストア公開 | ✅ 2026-09-11 公開 / 更新は API で自動化（`scripts/store_submit.py`） |

### 次にやること

1. ~~**Windows ビルドを本実装で再確認。**~~ **完了（2026-09-08）。**
   `workflow_dispatch` で `build_windows: true` を実行し、ビルド・同梱バイナリの
   取得と配置・起動確認まで通した（run 34283085312）。成果物は
   `archival-packager-windows-unsigned`（109MB）。

   7 月に止まっていたのは Actions の支出上限（`The job was not started because ...
   your spending limit needs to be increased`）で、これは解消済み。

   **最初の 1 回はスモークテストだけが落ちた。** `sf.exe` に `-home` を渡しておらず、
   ユーザ領域の署名 DB を探しに行って FATAL になっていた。アプリ（`core/siegfried.py`）は
   常に `-home` を渡すので、**確認だけがアプリと違う呼び方をしていた**ことになる。
   macOS 側は `release.zsh` で同じ直しを入れてあった（ea9a214）。同じ間違いが 2 か所にあった。

2. ~~**Windows のコード署名。**~~ **解決（2026-09-11）。** Microsoft ストアで
   公開したため、署名はストア側が行う。証明書の購入は不要になった。
   有償の OV/EV を買っても SmartScreen の警告は消えない（2024 年の仕様変更で、
   評判が貯まるまで警告が出る）ので、配布数の少ない研究用アプリでは
   ストア経由が唯一の現実解だった。

3. **Ghostscript のライセンス整理。** 同梱しない判断は暫定。PostScript/EPS の
   変換が実運用で必要になるなら、AGPL のまま同梱してよいか（あるいは Artifex の
   商用ライセンスを取るか）を決める必要がある。それまでは PATH 上の `gs` を使う。

4. **いつ Swift 版から切り替えるか。** 差分検証が通り、公証・同梱まで揃った。
   並行運用をいつまで続けるかを決められる状態にある。

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

これは [`nakamura196/archival-packager-swift`](https://github.com/nakamura196/archival-packager-swift)
の Swift 実装（macOS 専用、Developer ID 署名・公証済み）を、Windows にも配布できるように
作り直したもの。**現在はこちらが本体**で、Swift 版は更新を止めている
（2026-09-12 にリポジトリ名を入れ替えた。以前この名前は Swift 版が使っていた）。

**既存の Swift 実装は廃止しない。** 新実装が同等になるまではそちらが配布物であり、
かつ**差分検証の正解データを出す参照実装**でもある。

## 移植中に見つけた現行実装の問題

移植は現行実装の再読でもある。テストを書く過程で次が判明し、**Swift 側も修正済み**
（[archival-packager-swift#3](https://github.com/nakamura196/archival-packager-swift/pull/3) でマージ）。

1. **完全性確認が `failed` を `skipped` と報告する場合がある。**
   マニフェスト記載のファイルが全て存在しないとき、`checked == 0` の判定が先に効いて
   「マニフェストに有効な行がありません」を返す。ペイロードが消えている SIP を
   「検査せず飛ばした」と報告することになる。

2. **PREMIS の `eventIdentifierValue` を XML 生成のたびに振っていた。**
   同じ入力から 2 回生成すると別の出力になり、差分検証ができなかった。

3. **`accession.csv` の解析が引用フィールド内の改行を扱えない。**
   先に行で切ってからパースするため、原理的に扱えない。書き出し側は改行を含む値を
   引用で囲むので、自分が書いた CSV を読み戻せない状態だった。

### 誤検出だったもの

当初は上記に加えて 2 件を問題として挙げていたが、読み直したところ**誤りだった**。
記録として残しておく。

- **`structMap` の並び順**：`localizedStandardCompare` でソート済み。問題なし。
- **DFXML の `esc()` が `'` を扱わない**：`esc` は text ノードにのみ使われており、
  text 内の `'` はエスケープ不要。属性も `"` 区切りなので問題なし。

