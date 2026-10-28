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
[docs/usage-en.md](docs/usage-en.md).

### Design commitments

- **Nothing to install.** The tools used for identification and scanning ship
  with the application.
- **Originals are never modified.** Files are read only; preservation copies are
  written separately.
- **Output is meant to travel.** Package structure and descriptive metadata are
  matched field by field against the published specifications of Archivematica
  and AtoM, so that an organisation can move to those systems later.

### Known limits

Please read [既知の限界](docs/limits.md) (Japanese) before relying on this in
production. In short: normalisation covers images and PostScript/EPS only;
converted files are validated with JHOVE, but originals are not, and there is no
PDF/A validation (no veraPDF); the personal
information scan looks at five categories and has measured limits
([docs/pii-accuracy.md](docs/pii-accuracy.md)); and interoperability has been
checked against specifications but not yet against a running AtoM or
Archivematica instance ([docs/interoperability.md](docs/interoperability.md)).

The interface is Japanese and English.

### Documentation

| Document | Contents |
| --- | --- |
| [docs/manual/](docs/manual/en.md) | Using the window — every part of it, with screenshots (Japanese and English) |
| [docs/usage-en.md](docs/usage-en.md) | Operator's manual — the window and the command line |
| [docs/interoperability.md](docs/interoperability.md) | Field-by-field comparison with AtoM and Archivematica specifications |
| [docs/pii-accuracy.md](docs/pii-accuracy.md) | Measured precision and recall of the personal information scan |
| [docs/performance.md](docs/performance.md) | Time and memory at 100 to 50,000 files |
| [docs/rules.md](docs/rules.md) | Adding your own normalisation rules |
| [docs/atom-verification.md](docs/atom-verification.md) | How to verify against a running AtoM |
| [docs/development.md](docs/development.md) | Building and releasing (Japanese) |

### Licence

MIT for this application. Bundled third-party components keep their own
licences — see [NOTICE](NOTICE), which also explains the ClamAV (GPL-2.0)
source availability obligation.

---

# 日本語

ボーンデジタルの資料やデジタル化したファイルから、**SIP（受入パッケージ）** と
**AIP（長期保存パッケージ）** を作るデスクトップアプリです。macOS と Windows で動きます。

開発: 中村 覚（東京大学）・金 甫榮（人間文化研究機構）

保存のワークフローと、現場で必要なことの整理は金が、プログラムは中村が担当しました。
アプリは、そのワークフローのうち機械的に決められる部分を自動で行います。

## ダウンロード

| | |
| --- | --- |
| Windows | [Microsoft Store](https://apps.microsoft.com/detail/9N6XJD7THHPZ) |
| macOS | [Releases](https://github.com/nakamura196/archival-packager/releases/latest)（署名・公証済みの `.dmg`） |

ほかに入れるものはありません。ファイル形式の識別とウイルス検査の道具は、アプリに入っています。

## できること

- ファイル形式の識別（Siegfried と PRONOM）
- ウイルス検査（ClamAV）
- チェックサム（SHA-256）の記録
- 技術メタデータ（DFXML）
- AtoM 用の記述の表（ISAD(G)）、技術目録、受入記録
- 個人情報の候補の検出（マイナンバー・カード番号・電話番号・メールアドレス・郵便番号の 5 種類。結果は伏せ字で出します）
- SIP の作成（BagIt の形にもできます）
- AIP の作成（BagIt の形。METS と PREMIS で、いつ・何を・どの道具で行い・結果はどうだったかを残します）

大事にしていることは 3 つです。

- **インストールが要らないこと。** 道具はアプリに入っています
- **原本を書き換えないこと。** 原本は読むだけです。保存用の写しは別に作ります
- **よそへ持っていけること。** パッケージの形と記述は、Archivematica と AtoM の仕様に項目ごとに合わせてあります。後でそれらのシステムに移ることができます

## 使い方

- 画面の使い方（写真つき）: [docs/manual/](docs/manual/index.md)
- 手順書（画面とコマンドライン）: [docs/usage.md](docs/usage.md)

## 既知の限界

実際の業務で使う前に、できないことを確かめてください。主なものは次のとおりです。
詳しくは [docs/limits.md](docs/limits.md) にまとめています。

- 保存用の形式に変換するのは、画像と PostScript/EPS だけです。Office 文書・音声・動画は原本のまま保存します
- 利用のための縮小版やサムネイルは作りません
- ファイル形式として正しいかの検査（JHOVE）は、変換で作ったファイルだけに行います。受け入れた原本は検査しません
- 個人情報の検出は 5 種類だけです。「見つからなかった」は「個人情報が無い」という意味ではありません
- AtoM と Archivematica との受け渡しは、仕様と突き合わせただけで、動いているシステムではまだ確かめていません
- 画面は日本語と英語です。パッケージの中の見出しなどは日本語のままです

## そのほかの文書

| 文書 | 内容 |
| --- | --- |
| [docs/limits.md](docs/limits.md) | 既知の限界（詳しく） |
| [docs/interoperability.md](docs/interoperability.md) | AtoM・Archivematica の仕様との項目ごとの突き合わせ |
| [docs/pii-accuracy.md](docs/pii-accuracy.md) | 個人情報の検出の精度（実測） |
| [docs/performance.md](docs/performance.md) | 100〜50,000 件での時間とメモリ |
| [docs/rules.md](docs/rules.md) | 変換の規則を自分で足す方法 |
| [docs/atom-verification.md](docs/atom-verification.md) | 動いている AtoM で確かめる手順 |
| [docs/development.md](docs/development.md) | 開発と配布物の作り方（作る人向け） |

## ライセンス

このアプリは MIT ライセンスです。同梱している道具は、それぞれのライセンスに従います。
一覧は [NOTICE](NOTICE) にあります。
