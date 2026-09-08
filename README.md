# Archival Packager

born-digital / デジタル化ファイルから **SIP（受入）** と **AIP（長期保存）** を作る
デスクトップアプリ。**macOS と Windows の両方**を 1 つのコードベースから出す。

開発: 中村 覚（東京大学史料編纂所）・金 甫榮（人間文化研究機構）

保存ワークフローの設計と実務側の要件は金が、実装は中村が担当している。
アプリが自動化しているのは、策定したワークフローのうち機械的に決まる部分。

## 現在の状態

移植は完了し、署名済み `.app` まで通っている。

| | 状態 |
|---|---|
| コア移植（SIP / AIP 全モジュール） | ✅ 完了（320 テスト） |
| **現行実装との出力一致** | ✅ **差分なし** |
| UI（3 モード） | ✅ パッケージ版で起動確認済み |
| Developer ID 署名 | ✅ 未署名 Mach-O ゼロ |
| **公証（Apple）** | ✅ **Accepted / `spctl: accepted`**（ClamAV 同梱後・357MB） |
| フォーマット識別（siegfried） | ✅ 同梱・起動確認済み |
| ウイルス検査（ClamAV） | ✅ 同梱・自作シグネチャで検出まで確認 |
| 画像 → TIFF（Pillow） | ✅ アプリ内変換・通しで確認 |
| Windows ビルド | ✅ 本実装で通過（2026-09-08 の CI。同梱ツールの起動確認まで） |

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

2. **Windows のコード署名。** **未対応。** 無署名の `.exe` は SmartScreen が
   毎回「発行元不明」を出し、組織によっては管理者にブロックされる。macOS だけ
   公証済みで Windows が無署名だと、実質 Windows 版は配りにくい。
   OV / EV コードサイニング証明書の調達が要る（年額が発生する）。

   現状 CI が出す Windows 成果物は動作確認用であって配布物ではない。
   その旨をアーティファクト名にも入れてある（`...-windows-unsigned`）。

3. **Ghostscript のライセンス整理。** 同梱しない判断は暫定。PostScript/EPS の
   変換が実運用で必要になるなら、AGPL のまま同梱してよいか（あるいは Artifex の
   商用ライセンスを取るか）を決める必要がある。それまでは PATH 上の `gs` を使う。

4. **いつ Swift 版から切り替えるか。** 差分検証が通り、公証・同梱まで揃った。
   並行運用をいつまで続けるかを決められる状態にある。

## 使い方（開発）

```
uv sync
uv run flet run .                 # 開発モードで起動
uv run pytest                     # テスト
```

配布物のビルド（macOS）:

```
./scripts/fetch-binaries.zsh      # 同梱バイナリを取得（バージョン固定・起動確認まで）
./scripts/build.zsh macos         # .app（不要物を除外）
./scripts/sign.zsh                # Developer ID + hardened runtime + LICENSE/NOTICE
op run --env-file=<aip>/.env -- ./scripts/notarize.zsh   # .app の公証と staple
op run --env-file=<aip>/.env -- ./scripts/release.zsh    # .dmg 化・署名・公証・staple
op run --env-file=<aip>/.env -- ./scripts/release.zsh --publish   # + タグと GitHub Release
```

**この順序を崩さないこと。** `sign.zsh` はバンドルの中身を変えるので、後から
実行すると公証チケットが無効になる。`release.zsh` は開始前に署名・公証・
ライセンス表示の同梱を検証し、揃っていなければ何もせず止まる。

`--publish` を付けない限り、外部には何も出さない（dmg を作るところまで）。

同梱バイナリは `binaries/<os>/` に置き、リポジトリには含めない
（サイズが大きく、取得はスクリプトで再現可能）。方針は
「[外部ツールの同梱方針](#外部ツールの同梱方針)」を参照。

ウイルス定義 DB（数百 MB）は同梱しない。配布物が肥大化する上に、配った瞬間から
古くなる。アプリの「ウイルス定義データベース」から `freshclam` で取得する。

### 検証の順序

**UI を変えたら必ず起動させて確認する。** 依存の API 変更は静的な検査では
捕まらない。実際に Flet 0.86 で `FilePicker` が
コールバック方式から `await` 方式へ変わっており、パッケージ版を起動して
初めて `TypeError` で落ちた。

```
uv run pytest              # 速い。API 存在チェックも含む
uv run flet run .          # 開発モード。ビルドより遥かに速く同じ問題を捕まえる
./scripts/build.zsh macos  # 最終確認。FilePicker の問題はここでしか出なかった
```

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

## 外部ツールの同梱方針

同梱するものは `binaries/<os>/` に置き、リポジトリには含めない。取得は
`scripts/fetch-binaries.zsh`（macOS）と `scripts/fetch-binaries.ps1`（Windows）で
再現できる。**バージョンは両OSで必ず揃える。** 識別や検査の結果が環境で変わると、
同じ資料から作った保存パッケージの再現性が崩れるため。

| ツール | 用途 | 同梱 | 理由 |
|---|---|---|---|
| siegfried | フォーマット識別 | ✅ | 公式ビルドあり。`default.sig` も必須なので一緒に入れる |
| ClamAV | ウイルス検査 | ✅ | 公式ビルドあり。定義 DB はアプリから `freshclam` で取得 |
| Pillow | 画像 → TIFF | ✅（pip） | 外部プロセス不要。両OSで同一 wheel＝同一 libtiff |
| Ghostscript | PostScript/EPS → PDF | ❌ | **AGPL-3.0**。macOS 向け公式ビルドも無い |

### Ghostscript を同梱しない理由

Ghostscript は AGPL-3.0 で、MIT のこのアプリに同梱すると**配布物全体のライセンスを
どう扱うかの判断が要る**（Artifex は商用ライセンスを別途販売している）。加えて
macOS 向けの公式ビルドが配布されておらず、ソースからの構築が必要になる。
現行 Swift 版も同梱しておらず、PATH 上の `gs` を使う方式。同じ扱いにしてある。

`gs` が無い環境では PostScript/EPS は変換されず、原本がそのまま保存され、
report に**ツールが無いことが明示される**（資料が壊れている場合とは別の文言にしてある。
原因も対処も違うものを同じ文言にすると、report を読んでも区別がつかない）。

同梱する判断に切り替えるなら、ライセンス上の整理が先。

### 配布物に入れるライセンス表示

同梱物の一覧と条件は [`NOTICE`](NOTICE) にまとめてある。**`LICENSE` と `NOTICE` は
配布物に必ず同梱する。** とくに ClamAV は GPL-2.0 で、表示だけでなく
**ソースコードの入手手段を示す義務がある**（`NOTICE` に上流リリースの URL と
バージョンを明記してある。同梱しているのは改変していない公式ビルド）。

同梱は自動化してある。macOS は `sign.zsh` が `Contents/Resources/` へ置き、
`release.zsh` が dmg 化の前に有無を検証する。Windows は CI が実行ファイルの
隣に置き、スモークテストで存在を確認する。

ここに書いてあるのは事実の整理であって法的な判断ではない。組織として配布する
場合は、GPL コンポーネントの再頒布について所属機関の判断を仰ぐこと。

### ImageMagick をやめて Pillow にした理由

Swift 版は画像 → TIFF に macOS 内蔵の `sips` を使っていた。Windows には無いので
置き換えが要り、当初は ImageMagick を両OSに同梱する方針だった。取りやめた理由:

- **macOS 向けの公式な再配布可能ビルドが無い。** 配布されているのは Windows の
  portable ビルドと Linux の AppImage、あとはソースだけ。
- **その結果、両OSで別ビルドになる。** Homebrew 版と Windows portable 版では
  同梱される libtiff の版も揃わず、**同じ資料から出る派生物のバイト列が
  OS によって変わる**。どちらが「正」なのか説明できない。

Pillow なら wheel が両OS向けに同一版で提供され、libtiff も wheel 同梱の同じものが
使われる。外部プロセスが要らないので「配布先にツールが無くて変換されなかった」も
起きない。詳細は `core/image_normalize.py` の冒頭。

### 同梱で繰り返し踏んだ落とし穴

いずれも**開発機には該当ツールが入っているため、開発中は動いてしまう**。
配布先で初めて壊れる形なので、意識して潰す必要がある。

- **siegfried の `default.sig`**：同梱しないと配布先で署名 DB を見つけられない。
- **ClamAV の CVD 検証用証明書**：ClamAV 1.4 以降、これが無いと定義 DB を読めない。
  探索先はビルド時に焼き込まれた絶対パス（`/usr/local/clamav/etc/certs`）なので、
  同梱して `CVD_CERTS_DIR` で渡す。`--cvdcertsdir` だけでは freshclam 内部の
  DB 検証まで届かず、**取得は成功したように見えて検査が 0 件になる**。
- **`install_name_tool` は署名を壊す**。arm64 では署名が無効な実行ファイルは
  起動時に SIGKILL される（`rc=137`。エラーメッセージも出ない）。

## 移植中に見つけた現行実装の問題

移植は現行実装の再読でもある。テストを書く過程で次が判明し、**Swift 側も修正済み**
（[archival-packager#3](https://github.com/nakamura196/archival-packager/pull/3) でマージ）。

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

## 実装のドキュメント

- 技術検証と、そこで判明した落とし穴: [`spike/README.md`](spike/README.md)
- 各モジュールの設計判断はコード内のモジュール docstring に書いてある
  （「なぜそうしたか」を残す場所として、別ファイルよりコードの近くが良いと判断した）
