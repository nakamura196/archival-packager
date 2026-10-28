# 開発と配布

[README に戻る](../README.md)

アプリを作る人・配布物を作る人向けのメモです。使う人は [usage.md](usage.md) を見てください。

## 使い方（開発）

```
uv sync
uv run flet run .                 # 開発モードで起動
uv run pytest                     # テスト
uv run archival-packager check    # コマンドラインから（同梱ツールの状態を見る）
```

移管処理を自動化したい場合は、コマンドライン入口があります
（`sip` / `aip` / `inspect` / `check`）。**配布版では使えません**
（包んだアプリには引数が届かないため）。使い方は
[docs/usage.md](usage.md)（画面とコマンドラインの両方の手順書）。

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

配布物のビルドと申請（Windows / Microsoft Store）:

**手元に Windows 機が無いので、ビルドは GitHub Actions で行う。**
費用の都合で既定では走らないので、明示的に要求する。

```
gh workflow run ci.yml -f build_windows=true
gh run download <run-id> --name archival-packager-windows-msix-unsigned
op run --env-file=store/.env -- python scripts/store_submit.py --msix ./ArchivalPackager.msix
op run --env-file=store/.env -- python scripts/store_submit.py --check
```

MSIX に署名はしない。**ストアに MSIX で出すと Microsoft が署名し直す**ので、
こちらで証明書を買う必要がない。Identity は Partner Center が発行した値を
`packaging/windows/AppxManifest.xml.in` に入れてある。

掲載情報の正本は [store/listing-ja.md](../store/listing-ja.md) と
[store/listing-en.md](../store/listing-en.md) で、`store_submit.py` が両方を送る。
**思い出しながら書き直さない。** 言語を足すときは、`LISTINGS` と
`packaging/windows/AppxManifest.xml.in` の `<Resource Language=...>` を
両方直すこと（宣言していない言語の掲載情報は拒まれる）。突き合わせは
`tests/test_store_submit.py` が見ている。

申請 API を使うための設定（テナント・アプリ登録・権限・1Password）は
**1 回だけ**必要で、手順は [store/API設定手順.md](../store/API設定手順.md) にある。
API でできないこと（最初の 1 回の申請、プライバシーポリシー URL の設定）も
そこに書いてある。

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

同梱物の一覧と条件は [`NOTICE`](../NOTICE) にまとめてある。**`LICENSE` と `NOTICE` は
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

## 実装のドキュメント

- 利用者向けの手順書（画面とコマンドライン）: [`usage.md`](usage.md)
- 画面の使い方（写真つき、日英）: [`manual/`](manual/)
- 利用者向けの動画とページの作り方（ほかのアプリにも使える手順と落とし穴）: [`making-user-docs.md`](making-user-docs.md)
- 技術検証と、そこで判明した落とし穴: [`spike/README.md`](../spike/README.md)
- 各モジュールの設計判断はコード内のモジュール docstring に書いてある
  （「なぜそうしたか」を残す場所として、別ファイルよりコードの近くが良いと判断した）
