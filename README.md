# Archival Packager

A desktop application that builds OAIS information packages — a Submission
Information Package (SIP) and an Archival Information Package (AIP) — from
born-digital and digitised files. One codebase, macOS and Windows.

By Satoru Nakamura (The University of Tokyo) and Boyoung Kim
(National Institutes for the Humanities).
Kim designed the preservation workflow and the practitioner requirements;
Nakamura wrote the implementation. The application automates the parts of that
workflow that are mechanically determined.

[English](#english) · [日本語](#日本語)

## English

### Why it exists

Long-term digital preservation has an international standard, OAIS, and mature
open-source implementations such as Archivematica. Deploying and running them,
however, takes technical capacity that many small archives do not have. This
application covers the first step of that work — accession and packaging —
without requiring the operator to install anything or to use a command line.

### Download

| Platform | Where |
| --- | --- |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9N6XJD7THHPZ) |
| macOS | [Releases](https://github.com/nakamura196/archival-packager/releases/latest) (signed and notarised `.dmg`) |

No separate installation is needed. Format identification and virus scanning
tools are bundled.

### What it does

- Format identification with Siegfried against the PRONOM registry
- Virus scanning with ClamAV
- Checksums (SHA-256)
- Technical metadata in DFXML
- Descriptive spreadsheet for AtoM / ISAD(G), a technical inventory and an
  accession record
- Detection of candidate personal information (My Number, payment card,
  telephone, email and postal code — five categories only; results are masked)
- SIP, optionally serialised as a BagIt bag
- AIP as a BagIt bag with METS carrying embedded PREMIS, recording what was
  done, when, with which tool and with what outcome

There is also a command line (`archival-packager sip` / `aip` / `inspect` /
`check`) for scripted transfers and for CI. **It works only when running from
source** — arguments do not reach the packaged builds. See
[docs/usage.md](docs/usage.md).

### Design commitments

- **Nothing to install.** The tools used for identification and scanning ship
  with the application.
- **Originals are never modified.** Files are read only; preservation copies are
  written separately.
- **Output is meant to travel.** Package structure and descriptive metadata are
  matched field by field against the published specifications of Archivematica
  and AtoM, so that an organisation can move to those systems later.

### Known limits

Please read [既知の限界](#既知の限界) (Japanese) before relying on this in
production. In short: normalisation covers images and PostScript/EPS only;
there is no format validation (no JHOVE or veraPDF equivalent); the personal
information scan looks at five categories and has measured limits
([docs/pii-accuracy.md](docs/pii-accuracy.md)); and interoperability has been
checked against specifications but not yet against a running AtoM or
Archivematica instance ([docs/interoperability.md](docs/interoperability.md)).

The interface is Japanese and English.

### Documentation

| Document | Contents |
| --- | --- |
| [docs/usage.md](docs/usage.md) | Operator's manual — the window and the command line |
| [docs/interoperability.md](docs/interoperability.md) | Field-by-field comparison with AtoM and Archivematica specifications |
| [docs/pii-accuracy.md](docs/pii-accuracy.md) | Measured precision and recall of the personal information scan |
| [docs/performance.md](docs/performance.md) | Time and memory at 100 to 50,000 files |
| [docs/rules.md](docs/rules.md) | Adding your own normalisation rules |
| [docs/atom-verification.md](docs/atom-verification.md) | How to verify against a running AtoM |

### Licence

MIT for this application. Bundled third-party components keep their own
licences — see [NOTICE](NOTICE), which also explains the ClamAV (GPL-2.0)
source availability obligation.

---

# 日本語

born-digital / デジタル化ファイルから **SIP（受入）** と **AIP（長期保存）** を作る
デスクトップアプリ。**macOS と Windows の両方**を 1 つのコードベースから出す。

開発: 中村 覚（東京大学）・金 甫榮（人間文化研究機構）

保存ワークフローの設計と実務側の要件は金が、実装は中村が担当している。
アプリが自動化しているのは、策定したワークフローのうち機械的に決まる部分。

## ダウンロード

| | |
| --- | --- |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9N6XJD7THHPZ) |
| macOS | [Releases](https://github.com/nakamura196/archival-packager/releases/latest)（署名・公証済みの `.dmg`） |

導入作業は要らない。識別と検査に使う外部ツールは同梱している。

## 既知の限界

**できないことを先に書く。** 実運用で「できるはず」と思って使われると困るため。

### 正規化（保存用フォーマットへの変換）の範囲が狭い

**組み込みの規則は 2 系統だけ**で、それ以外は原本のまま保存される。

| 入力 | 出力 | 使う道具 | 実際に動くか |
|---|---|---|---|
| PNG / JPEG / GIF / BMP（16 PUID） | TIFF | Pillow（アプリ内蔵） | 常に動く |
| PostScript / EPS（8 PUID） | PDF | Ghostscript | **同梱していない**ので、PATH に `gs` がある環境だけ |

つまり、既定では **Office 文書・音声・動画・CAD は変換されない。**
原本のまま保存され、その旨が report に記録される。

**規則は利用者が足せる。** 書式と置き場は [docs/rules.md](docs/rules.md) を参照。
足せるのは「利用者の環境に既にあるコマンドを呼ぶ」規則で、アプリが外部のコードを
読み込むことはない。使った規則表そのものは AIP に同梱されるので、後から
「どの規則で作られたか」をパッケージ単体で辿れる。

ただし**足りないのは規則表ではなく、変換する道具のほう**である。範囲を広げるには
LibreOffice（Office → PDF/A）や ffmpeg（音声・動画）が要るが、同梱すると
「インストール不要」が壊れ、ライセンスの判断も要る（Ghostscript を同梱しない理由は後述）。

### 利用用の派生物とサムネイルは作らない

`DerivativePurpose` に `ACCESS` を定義してあるが、**作らない。**

利用用の派生物とサムネイルは DIP（提供用情報パッケージ）に属するものであり、
このアプリが作るのは SIP と AIP だけである。品質を落とした派生物を AIP に入れても
保存上の価値はなく、容量が増えるだけになる。提供は下流のシステムの役割と考えている。

### 形式の適合性検査（validate）は行っていない

保存用に変換した PDF が PDF/A として妥当か、TIFF が TIFF の仕様に適合しているかを
**確かめていない。** Archivematica は veraPDF や JHOVE で検査している。
相当する機能は入っていない（どちらも Java 製で、同梱できないため。
「[外部ツールの同梱方針](#外部ツールの同梱方針)」を参照）。

識別（これは何形式か）と検証（その形式として正しいか）は別の工程で、
本アプリが行っているのは識別までである。

**やっているのは「開き直せるか」だけ。** 変換で作った TIFF は Pillow で、
PDF は pypdf で、もう一度開いて読めることを確かめている。読めなければ、その
派生物は AIP に入れず、原本のまま保存して report に警告を出す。中途半端な
派生物を保存用として記録するほうが、変換できなかったと記録するより悪いため。

結果は PREMIS の `validation` イベントとして METS に残る（成功・失敗・
「読み戻す道具が無くて確認していない」の 3 通りを書き分ける）。ただし
**これは veraPDF / JHOVE の代わりにはならない。** 開けたことは、その形式の
仕様に適合していることを意味しない。

### 相互運用は仕様との突合までしか確かめていない

パッケージ構造と記述メタデータは、Archivematica と AtoM の公式仕様と項目単位で
突き合わせてある（[docs/interoperability.md](docs/interoperability.md)）。
**ただし、実際に両システムに読み込ませたわけではない。**
実機で確かめる手順は [docs/atom-verification.md](docs/atom-verification.md) に置いた。

### 個人情報の検出は 5 種別だけ

マイナンバー、クレジットカード番号、電話番号、メールアドレス、郵便番号。
**氏名・住所・生年月日・口座番号などは見ていない。**

適合率・再現率の実測値と、原理的な限界（12 桁は約 1/11 で検査用数字を偶然通る、など）は
[docs/pii-accuracy.md](docs/pii-accuracy.md) にある。
**「検出されなかった」は「個人情報が無い」ではない。**

### その他

- ウイルス定義データベースは同梱していない（アプリの設定画面から取得する）
- 画面は日本語と英語。**report や CSV の見出しなどパッケージの中身は日本語のまま**
  （言語で変わると、別の言語で作ったパッケージを読み戻せなくなるため）
- `datetime` を素のまま扱っている箇所が残っている（ruff の DTZ 規則は未有効）。
  Windows で実際に事故を起こした種類なので、順次直す
- 規模の目安は [docs/performance.md](docs/performance.md)。5 万件で約 110 秒・430 MiB

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
[docs/usage.md](docs/usage.md)（画面とコマンドラインの両方の手順書）。

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

## 実装のドキュメント

- 利用者向けの手順書（画面とコマンドライン）: [`docs/usage.md`](docs/usage.md)
- 技術検証と、そこで判明した落とし穴: [`spike/README.md`](spike/README.md)
- 各モジュールの設計判断はコード内のモジュール docstring に書いてある
  （「なぜそうしたか」を残す場所として、別ファイルよりコードの近くが良いと判断した）
