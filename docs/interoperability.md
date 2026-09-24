---
title: "相互運用の検証 — AtoM / Archivematica"
eyebrow: Archival Packager 技術資料
---

最終更新: 2026年9月12日（午後 — 是正の記録を追記）

---

## 結論

**このアプリの出力を、AtoM と Archivematica の公式仕様に 1 項目ずつ突き合わせた
（9月12日午前）。37 項目のうち 10 項目がずれていた。同日午後、そのうち 7 項目を
是正した。作業中に 1 項目を新たに見つけたので、いま残っているずれは 4 件である。**

是正したのは次の 7 つ（詳細は「5. ずれの一覧」）。

1. **AtoM に渡す CSV を別ファイルとして足した**（`metadata/submissionDocumentation/atom-import.csv`）。
   列名は AtoM の機械名、`legacyId` つき、SIP 全体 → ファイル 1 件ずつの階層つき。
   **`description.csv` の列名は変えていない。** 担当者が書き込むシートであり、
   `core/sip_reader.py` がこの見出しで過去のパッケージを読み戻しているため。
2. **BagIt で梱包したとき、`metadata.csv` のパスが `data/` で始まるようにした。**
3. **`checksum.sha256` を Archivematica が見る `metadata/` 直下にも置き、
   パス表記を公式例に合わせた**（`objects/` の接頭辞を外した）。

残っている 4 件は、いずれも**新しい機能か、今回触れない範囲の変更が要る**もの
（PREMIS rights・AtoM の `culture` 列）か、
**直す必要が無いと判断したもの**（tagmanifest のアルゴリズム差）である。

**なお、この検証も是正も、仕様文書との突合にとどまる。実際に AtoM /
Archivematica を動かして読み込ませたわけではない。** 限界は末尾に明記した。
「直した」と書いてあるのは**仕様の文面に合わせた**という意味であって、
実機で通ることを確かめた、という意味ではない。

---

## 1. 参照した仕様

すべて 2026年9月12日に参照。

| 仕様 | 版 | URL |
| --- | --- | --- |
| AtoM ISAD(G) CSV インポートの列名（正本） | qa/2.x | <https://github.com/artefactual/atom/blob/qa/2.x/lib/task/import/example/isad/example_information_objects_isad.csv> |
| AtoM CSV import | 2.8 | <https://www.accesstomemory.org/en/docs/2.8/user-manual/import-export/csv-import/> |
| AtoM CSV validation | 2.8 | <https://www.accesstomemory.org/en/docs/2.8/user-manual/import-export/csv-validation/> |
| AtoM ISAD(G) データ入力テンプレート（項目名 → 列名の対応） | 2.8 | <https://www.accesstomemory.org/en/docs/2.8/user-manual/data-templates/isad-template/> |
| Archivematica Transfer（転送の構造・予約名・チェックサム） | 1.16 | <https://github.com/artefactual/archivematica-docs/blob/1.16/user-manual/transfer/transfer.rst> |
| Archivematica Import metadata（metadata.csv の形式） | 1.16 | <https://github.com/artefactual/archivematica-docs/blob/1.16/user-manual/transfer/import-metadata.rst> |
| Archivematica AIP structure | 1.16 | <https://www.archivematica.org/en/docs/archivematica-1.16/user-manual/archival-storage/aip-structure/> |
| BagIt File Packaging Format | RFC 8493 | <https://tools.ietf.org/html/rfc8493> |

列名の正本として、ドキュメントのページではなく **AtoM に同梱されている例 CSV
そのもの**を使った。ドキュメントは代表的な列しか挙げていないので、全列を知るには
コードに入っているファイルを見るしかない。AtoM のドキュメントにも
「You can also find all example CSV import templates included in your AtoM
installation, in: `lib/task/import/example`」と書いてある。

---

## 2. AtoM の ISAD(G) CSV との突合

### 2.1 何が食い違っていたか（9月12日午前）

AtoM が期待するのは **camelCase の機械名 56 列**（`legacyId, parentId,
qubitParentSlug, accessionNumber, identifier, title, …`）。
このアプリが出していたのは **人間向けのラベル 26 列**（`Parent ID, Identifier,
Title, Archive Creator, …`）だけだった。

**一致した列名: 0 / 26。**

AtoM の CSV validation は未知の列について
「Unrecognized columns will be ignored by AtoM when the CSV is imported」
と警告する。つまり **エラーにならない。** 取り込みは成功し、
ほぼ空の記述レコードが 1 件できるだけになる。

### 2.1b どう直したか（9月12日午後）

**列名を差し替えるのではなく、AtoM に渡す CSV を 1 本足した。**

    metadata/submissionDocumentation/description.csv   人が書き込む（列名は従来のまま）
    metadata/submissionDocumentation/atom-import.csv   AtoM に渡す（機械名・階層つき）

`description.csv` の見出しを機械名に変えなかったのは、**変えると公開済みの
アプリで作ったパッケージを `core/sip_reader.py` が読み戻せなくなる**ため。
読み戻せないと、過去の SIP から AIP を作れなくなる。加えて、このシートは
担当者が手で記入するものなので、`parentId` のような機械名にすると人が読めない。

`atom-import.csv` の中身は `description.csv` と同じ値から機械的に作る
（`core/spreadsheets.py` の `_whole_values`）。二重管理にはならない。

書き方が 1 つだけ他の CSV と違う。**この CSV だけ BOM 無し・LF で書く。**

- 改行: AtoM のドキュメントは「AtoM's CSV import will expect Unix-style line
  breaks (`\n`)」と明記し、CSV validation は CRLF を「unintended blank rows」の
  原因として名指ししている。他の CSV は Excel 互換のため CRLF のままにしている。
- BOM: UTF-8 の BOM は ERROR にならないが、**AtoM が剥がすとはどこにも書いていない。**
  剥がされないまま読まれると先頭の列名が `legacyId` と認識されず、
  未知の列として捨てられる。それでは `legacyId` を出した意味が消える。
  この CSV は人が Excel で開くものではないので、確実な側に倒した。

### 2.2 列ごとの対応表

意味の上では 26 列すべてに対応先がある。名前を変えるだけで移せる。
**この表がそのまま `core/spreadsheets.py` の `ATOM_MACHINE_COLUMNS` になっている**
（並びが一致することは `test_interoperability.py` が assert している）。

| description.csv の見出し（人向け・不変） | atom-import.csv で出す列（AtoM の機械名） |
| --- | --- |
| Parent ID | `parentId` |
| Identifier | `identifier` |
| Title | `title` |
| Archive Creator | `eventActors` |
| Date expression | `eventDates` |
| Date start | `eventStartDates` |
| Date end | `eventEndDates` |
| Level of description | `levelOfDescription` |
| Extent and medium | `extentAndMedium` |
| Scope and content | `scopeAndContent` |
| Arrangement (optional) | `arrangement` |
| Accession number | `accessionNumber` |
| Appraisal, destruction, and scheduling information (optional) | `appraisal` |
| Name access points (optional) | `nameAccessPoints` |
| Geographic access points (optional) | `placeAccessPoints` |
| Conditions governing access (optional) | `accessConditions` |
| Conditions governing reproduction (optional) | `reproductionConditions` |
| Language of material (optional) | `language` |
| Physical characteristics & technical requirements affecting use (optional) | `physicalCharacteristics` |
| Finding aids (optional) | `findingAids` |
| Related units of description (optional) | `relatedUnitsOfDescription` |
| Archival history (optional) | `archivalHistory` |
| Immediate source of acquisition or transfer (optional) | `acquisition` |
| Archivists' note (optional) | `archivistNote` |
| General note (optional) | `generalNote` |
| Description status | `descriptionStatus` |

### 2.3 こちらが一切出していない AtoM の列（29 列）

**`legacyId` は 2026年9月12日に出すようにした**ので、この一覧から外れて 30 列から
29 列になった。

`qubitParentSlug`, `repository`, `accruals`, `script`, `languageNote`,
`locationOfOriginals`, `locationOfCopies`, `publicationNote`, `digitalObjectPath`,
`digitalObjectURI`, `subjectAccessPoints`, `genreAccessPoints`,
`descriptionIdentifier`, `institutionIdentifier`, `rules`, `levelOfDetail`,
`revisionHistory`, `languageOfDescription`, `scriptOfDescription`, `sources`,
`publicationStatus`, `physicalObjectName`, `physicalObjectLocation`,
`physicalObjectType`, `alternativeIdentifiers`, `alternativeIdentifierLabels`,
`eventTypes`, `eventActorHistories`, `culture`

**残っているうち実務で効くのは `culture`（記述そのものの言語）である。**
無いと AtoM の既定の言語で取り込まれるので、日本語の記述を英語サイトに入れても
`ja` が付かない。今回出さなかったのは、**記述の言語を入力する画面が無い**ため。
`ja` を決め打ちすると、英語で記述する利用者の記録に誤った言語が付く。
「入力欄を足す」という機能追加の話なので、ずれとして残した（下の表の 11）。

なお `legacyId` については、AtoM の `CsvLegacyIdValidator` が欠落時に WARNING を
出し「Future CSV updates may not match these records」と説明する。つまり
**CSV を直して入れ直すと、既存のレコードを更新せず新しいレコードを作る。**
移管を繰り返す運用では同じ資料が何件も並ぶので、これは必ず出す必要があった。
値は SIP 全体が識別子そのもの、ファイル行が `<識別子>/<相対パス>` で、
同じ資料を作り直しても同じ値になる（＝入れ直しても重複しない）。

### 2.4 仕様どおりだったところ・改めたところ

| 項目 | 仕様 | このアプリ | 判定 |
| --- | --- | --- | --- |
| 文字コード | UTF-8 必須 | UTF-8 | 一致 |
| BOM | UTF-8 の BOM は ERROR にならない（ERROR は「BOM があるが UTF-8 ではない」場合のみ） | `description.csv` は BOM 付き、`atom-import.csv` は BOM 無し | 一致（後述） |
| 行末 | 「AtoM's CSV import will expect Unix-style line breaks (`\n`)」 | `atom-import.csv` のみ LF。他は CRLF | 一致（2026-09-12 に改めた） |
| `levelOfDescription` の値 | AtoM のタクソノミーの語（Fonds / Series / File / Item …） | 全体が `File`、ファイルが `Item` | 一致 |
| 引用規則 | RFC 4180 | Python 標準の `csv` に委譲 | 一致 |

**行末は、午前の突合で「RFC 4180（CRLF）。特段の制約なし」と書いていたが誤りだった。**
AtoM のドキュメントは Unix 改行を期待すると明記しており、CSV validation は
CRLF を「unintended blank rows」の原因として名指ししている。AtoM へ渡す
`atom-import.csv` は LF で書く。人が Excel で開く `description.csv` は CRLF のまま。

BOM は「ERROR にならない」だけで「剥がされる」とは書かれていない。残ったまま
読まれると先頭の列名（`legacyId`）が未知の列として捨てられるので、
`atom-import.csv` では付けない。

### 2.5 階層について

`atom-import.csv` は **SIP 全体の行 1 つと、ファイル 1 件ごとの行**を出す。
各ファイル行の `parentId` が全体行の `legacyId` を指す。AtoM は
「if your CSV is not properly ordered with parent records appearing before their
children, your import will fail」とするので、親の行を必ず上に置いている。

これで **AtoM 側でファイル単位の目録を作り直さずに済む。** ファイル行には
識別子（相対パス）・タイトル（ファイル名）・数量・年代に加え、
フォーマット名と PRONOM ID を `physicalCharacteristics`（ISAD(G) 3.4.4
「物理的特徴と技術的条件」）に入れている。将来その資料を開けるかどうかは、
フォーマットが分からないと判断できないため。

`description.csv` の方は従来どおり **SIP 全体で 1 行**である。担当者に書かせるのは
移管 1 件ぶんの記述で、ファイルごとに書かせるものではない。

---

## 3. Archivematica の転送（transfer）との突合

### 3.1 非 bag の SIP（standard transfer として）

Archivematica の transfer ドキュメントは、標準転送について
「including an `objects` and `metadata` directory including Archivematica's
special files such as metadata.csv」と書いており、
**トップレベルに `objects/` と `metadata/` を置く形を明示的に認めている。**

| 項目 | Archivematica の仕様 | このアプリ | 判定 |
| --- | --- | --- | --- |
| トップレベル | すべての資料が 1 つの最上位ディレクトリに入る | パッケージ 1 つ | 一致 |
| 原本の置き場 | `objects/` | `objects/`（相対パス保持） | 一致 |
| 予約名 | `objects` / `metadata` / `logs`。`metadata` は「must not be used for anything else」 | `objects/` と `metadata/` のみ。他用途に使っていない | 一致 |
| `metadata.csv` の位置 | `metadata/` 直下 | `metadata/metadata.csv` | 一致 |
| `metadata.csv` の先頭列 | `filename`（必ず先頭） | `filename` | 一致 |
| `metadata.csv` の 2 列目以降 | `dc.要素` / `dcterms.要素` 以外は `MDTYPE="OTHER"` になり AtoM へ渡らない | `dc.` / `dcterms.` のみ 7 列 | 一致 |
| ファイル行のパス | 「must always start with `objects/`」 | `objects/a.txt` 等 | 一致 |
| 提出書類 | `metadata/submissionDocumentation/` | 同じ | 一致 |
| 文字コード | 「Archivematica can only read CSV files that have been encoded in UTF-8」 | UTF-8（BOM 付き） | 一致（注） |
| 全体行のパス | 例はファイル `objects/audio/bird.mp3` かディレクトリ `objects/CoastNews-1964-01-02`。末尾スラッシュだけの形の例は無い | `objects`（ディレクトリを名指し） | 一致（2026-09-12 に是正） |
| `checksum.sha256` の位置 | 「Checksum files are placed in the `metadata` directory」 | `metadata/checksum.sha256` | 一致（2026-09-12 に是正） |
| `checksum.sha256` のパス表記 | 「the checksum, followed by two spaces, followed by the file path」。公式例（`beihai.tif`）のパスに `objects/` の接頭辞は無い | `<hash>␣␣<objects/ からの相対パス>` | 一致（2026-09-12 に是正） |

（注）BOM について Archivematica のドキュメントは何も述べていない。UTF-8 であることは
満たしているが、**Python の `csv` で BOM を剥がさずに読むと先頭列名が
`﻿filename` になる**というのはよくある落とし穴で、Archivematica 側が
剥がしているかどうかは仕様からは分からない。実機での確認が要る項目。

#### `checksum.sha256` が 2 箇所にあること（2026-09-12）

置き場を「移した」のではなく、**同じ内容をパス基準だけ変えて 2 本置いた。**
読み手が 2 人いて、求める形が違うため。

    metadata/checksum.sha256                         Archivematica が読む。
                                                     `<hash>␣␣<objects/ からの相対パス>`
    metadata/submissionDocumentation/checksum.sha256 このアプリの `core/fixity.py` が読む。
                                                     `<hash>␣␣objects/<相対パス>`（SIP ルート基準）

前者だけにすると、**このアプリ自身の完全性確認がマニフェストを見つけられず、
「検査していない」状態のまま AIP ができる。** 後者だけだと、Archivematica の
*Verify transfer checksums* が走らない（＝検証されずに素通りする）。
`core/fixity.py` は今回の作業で触れない範囲にあり、旧形式の SIP もこの位置・
この表記を前提にしているので、両方を残すのが唯一の解だった。
どちらも同じ 1 つのリストから作るので、内容がずれることはない。

**bag には `checksum.sha256` を置かない。** bag は `manifest-sha256.txt` が同じ
役目を果たす。二重に持つと、食い違ったときにどちらが正なのか分からなくなる。

### 3.2 BagIt で梱包した SIP（unzipped bag transfer として）

| 項目 | Archivematica / BagIt の仕様 | このアプリ | 判定 |
| --- | --- | --- | --- |
| bag の適合性 | RFC 8493。転送の早い段階で検証される | `bagit` ライブラリで生成・検証が通る | 一致 |
| タグファイル | `bagit.txt` / `bag-info.txt` / manifest / tagmanifest | 4 つとも生成 | 一致 |
| マニフェストのパス | `data/` からの相対 | `data/objects/文書/b.txt` 等 | 一致 |
| ペイロードの構造 | 資料は必ず `data/` の中 | `data/objects/` | 一致 |
| 提出書類 | 「The `submissionDocumentation` directory should be nested inside the metadata directory」 | `data/metadata/submissionDocumentation/` | 一致 |
| `metadata.csv` のパス | 「the filename path must always begin with `data`」。公式例は `data/beihai.tif` | `data/objects/a.txt` | 一致（2026-09-12 に是正） |

**ここは影響が大きかった。** 直す前は bag でも `objects/a.txt` と書いており、
その行に対応する実体が転送内に無いため、**ファイル単位の記述メタデータが
1 件も紐づかなかった。** bag 化するかどうかで `filename` の基点を変えるようにした
（`core/spreadsheets.metadata_base`）。

構造化入力（`objects/` を持つフォルダ）を受け取ったときは、担当者が書いた
`metadata.csv` をそのまま引き継ぐ。ただし **`filename` 列の基点だけは
bag / 非 bag に合わせて直す**（`core/spreadsheets.rebase_metadata_csv`）。
記述の中身には触らない。「書いた人の内容を上書きしない」ことと
「Archivematica が読める」ことは両立させる必要があり、片方だけ立てると
どちらかが黙って壊れる。

---

## 4. AIP と Archivematica の AIP 構造の突合

Archivematica の AIP は bag で、`data/` の下に `METS.<uuid>.xml`、`README.html`、
`logs/`、`objects/`（原本と保存用マスタ、および `submissionDocumentation/`）、
任意で `thumbnails/` を置く。

| 項目 | Archivematica の AIP | このアプリ | 判定 |
| --- | --- | --- | --- |
| 全体の形 | BagIt bag | BagIt bag | 一致 |
| METS の名前と位置 | `data/METS.<uuid>.xml` | 同じ | 一致 |
| 原本 | `data/objects/` | 同じ | 一致 |
| 保存処理ログ | `data/logs/` | 同じ | 一致 |
| 提出書類 | `data/objects/submissionDocumentation/` | 同じ | 一致 |
| METS のセクション | dmdSec / amdSec / fileSec / structMap | 4 つとも出力 | 一致 |
| PREMIS | object / event / agent で来歴を表す | 3 つとも出力（PREMIS v3 名前空間） | 一致 |
| structMap | 物理構造 | `TYPE="physical"` | 一致 |
| **`data/README.html`** | AIP の構造を説明する HTML | **書いている**（2026-09-12〜） | 一致 |
| **tagmanifest** | 公式例は `tagmanifest-md5.txt` | `tagmanifest-sha256.txt` | **不一致（実害なし）** |
| **PREMIS rights** | `metadata/rights.csv` から生成 | `rights.csv` を作らず `rightsMD` も出さない | **不一致** |

tagmanifest のアルゴリズム差は BagIt 仕様上どちらも適法で、Archivematica が
MD5 を要求しているわけではない。SHA-256 に揃えているのは意図した選択
（`sip_builder` の冒頭に理由が書いてある）。

なお Archivematica は AIP 内で保存用マスタのファイル名に UUID を付ける
（`objects/` の中で原本と派生物を区別するため）。このアプリがどうしているかは、
外部変換ツールが要るため今回の機械的検査の対象にしていない。

---

## 5. ずれの一覧と、それぞれの影響

下の 5.2（残っているもの）が `tests/test_interoperability.py` の `KNOWN_GAPS` と
1 対 1 で対応する。5.1（是正したもの）は同じファイルの assert に対応する。
**片方だけ直すと、また「主張と検証の乖離」が生まれる。必ず両方直すこと。**

### 5.1 是正したもの（2026年9月12日）

**直したものは `KNOWN_GAPS` から外し、上の各節の assert へ移してある。**
壊れたら `tests/test_interoperability.py` が落ちる。

| # | 領域 | 直す前 | いま | 何をしたか |
| --- | --- | --- | --- | --- |
| 1 | AtoM: 列名 | 人間向けラベル 26 列のみ | `atom-import.csv` を追加（機械名 27 列） | `description.csv` は**変えず**、AtoM 用の CSV を別ファイルで出すようにした。列名を変えると過去のパッケージを読み戻せなくなるため |
| 2 | AtoM: `legacyId` | 出していない | 全行に出す | 全体行は識別子、ファイル行は `<識別子>/<相対パス>`。作り直しても同じ値になるので、入れ直しても重複しない |
| 3 | AtoM: 階層 | SIP 全体で 1 行 | 全体 1 行 + ファイル 1 行ずつ | 各ファイル行の `parentId` が全体行の `legacyId` を指す。親の行を必ず上に置く |
| 4 | AM: `metadata.csv` の全体行 | `objects/` | `objects` | 末尾スラッシュを外し、転送内に実在するディレクトリを名指しする形にした |
| 5 | AM: bag の `metadata.csv` | `objects/a.txt` | `data/objects/a.txt` | bag 化するかどうかで `filename` の基点を変えるようにした。記入済みの `metadata.csv` を引き継ぐ場合も、パス列だけ合わせ直す |
| 6 | AM: `checksum.sha256` の位置 | `metadata/submissionDocumentation/` のみ | `metadata/` 直下にも置く | 移動ではなく 2 本に分けた。理由は 3.1 の補足を参照（`core/fixity.py` と旧 SIP が旧位置を前提にしている） |
| 7 | AM: `checksum.sha256` のパス表記 | `objects/<相対パス>` | `metadata/` 直下の方は接頭辞なし | 公式例（`beihai.tif`）に合わせた。`submissionDocumentation/` 側は従来どおり `objects/` 付き（読み手が違う） |
| 9 | AIP: `data/README.html` | `data/logs/README.txt` のみ | `data/README.html` を書く | 日本語と英語で、これが何か・何が入っているか・どう読むか・**何を確認していないか**を書いた自己完結の HTML。外部の CSS も画像も参照しない。`bagit.make_bag` の前に書くので payload に入り、`manifest-sha256.txt` に載る。記述メタデータは複写していない（正本は METS の dmdSec で、2 か所に書くといずれ食い違う） |

**後方互換について。** 1〜7 はファイルの配置と中身が変わる（9 は
ファイルを 1 つ足すだけなので、読み戻しには影響しない）。
`core/sip_reader.py` は**新旧どちらの形も読む**ようにした。

- `checksum.sha256`: 新（`metadata/` 直下・接頭辞なし）と
  旧（`submissionDocumentation/` 配下・`objects/` 付き）の両方を見る
- `metadata.csv` の `filename`: `objects`・`objects/`・`objects/<rel>`・
  `data/objects/<rel>` のいずれも受け付ける
- `description.csv` の列見出しは**変えていない**ので、読み戻しの土台は無傷

新旧どちらの SIP も読めることは `tests/test_sip_reader_compat.py` が固定している。
旧形式の SIP は、旧版が書いていたバイト列をテスト内で組み立てて確かめている。

### 5.2 残っているもの

| # | 領域 | こちらの出力 | 相手の仕様 | 影響 | なぜ残したか |
| --- | --- | --- | --- | --- | --- |
| 8 | AM: PREMIS rights | `rights.csv` を作らず `rightsMD` も出さない | `metadata/rights.csv` から生成 | 中。利用条件が機械的に読めない | **機能追加が要る。** 権利情報（利用条件・根拠・期間）を入力する画面が無く、入力元が無いまま空の `rights.csv` を出しても意味がない |
| 10 | AIP: tagmanifest | `tagmanifest-sha256.txt` | 公式例は `tagmanifest-md5.txt` | なし | **直さない。** BagIt 仕様上どちらも適法で、Archivematica が MD5 を要求しているわけではない。SHA-256 に揃えているのは意図した選択 |
| 11 | AtoM: `culture` 列 | 出していない | 記述そのものの言語（`ja` / `en` …） | 小。AtoM の既定の言語で取り込まれる | **機能追加が要る。** 記述の言語を入力する欄が無い。`ja` を決め打ちすると英語で記述する利用者の記録に誤った言語が付く。9月12日の是正作業中に、1〜3 を直しても残ることが分かったので新たに記録した |

---

## 6. この検証の限界

**ここに書いたことは、突合も是正も、すべて公式ドキュメントとの机上の作業である。**
以下は確かめていない。書かれていないことを「確かめた」と読まないこと。

- **AtoM も Archivematica も動かしていない。** 実際に CSV を読み込ませたことも、
  転送を流したこともない。「一致」「是正した」と書いた項目も、**仕様の文面と
  出力が合っている**という意味でしかない。
- **ドキュメントに書かれていない挙動は分からない。** 9月12日の是正でも、
  次の 3 点は仕様から決められず、**安全と思われる側に倒した**だけである。
  - `atom-import.csv` の BOM。AtoM が剥がすなら BOM 付きでも通る。剥がさない
    なら先頭列が死ぬ。**剥がすとは書かれていない**ので付けなかった。
  - `metadata.csv` の全体行。`objects` と書けば必ずディレクトリとして扱われる
    のか、そもそも全体への記述という仕組みがあるのかは、ドキュメントに無い。
    **ディレクトリを指す例に形を合わせた**にとどまる。
  - bag における `checksum.sha256`。bag では置いていない（`manifest-sha256.txt`
    があるため）。bag でも外部チェックサムを要求されるかは分からない。
- **版が固定されていない。** AtoM 2.8 / Archivematica 1.16 を見た。
  利用者が動かすのが別の版なら、結論は変わりうる。特に AtoM の列は
  版によって増減する。
- **不一致の「影響」は推定である。** 「全列が無視される」「紐づかない」は
  仕様の記述からの推論であって、実際のエラー画面を見たわけではない。
  同じく、**是正によって実際に取り込めるようになったかも未確認である。**
- **正規化（フォーマット変換）を伴う AIP は検査していない。** 外部ツール
  （Ghostscript 等）の有無で構造が変わり、環境によって結果が変わってしまうため。
  保存用マスタのファイル名の付け方は未検証のまま。

---

## 7. 実機で確かめるなら、次に何をするか

優先順に並べた。1 と 2 だけでも、上の推定の大半は裏が取れる。

1. **AtoM を Docker で立てて、`atom-import.csv` を `csv:import` に流す。**
   確かめること: (a) `php symfony csv:check-import`（CSV validation）が
   `CsvColumnNameValidator` で未知の列を出さないか、(b) 階層（全体 → ファイル）が
   AtoM 上で組まれるか、(c) 同じ CSV をもう一度流したとき、レコードが増えずに
   更新されるか（`legacyId` が効いているか）。
2. **同じ CSV に BOM を付けた版でも流す。** 先頭列名が `legacyId` と認識されるか
   どうかで、BOM を外した判断が正しかったかが決まる。ついでに、
   旧来の `description.csv` をそのまま流すと本当に全列が捨てられるかも見る。
3. **Archivematica を `am` / compose で立て、非 bag の SIP を standard transfer
   として流す。** 確かめること: (a) `metadata.csv` の全体行 `objects` が
   どう扱われるか、(b) `metadata/checksum.sha256` で *Verify transfer checksums*
   が走るか、パスの基点は `objects/` からの相対で合っているか、
   (c) `metadata/submissionDocumentation/` の方の `checksum.sha256` が
   悪さをしないか（同名のファイルが 2 つあることの影響）。
4. **同じ資料を bag にして unzipped bag transfer として流す。**
   `metadata.csv` のパスを `data/objects/…`（現在）と `objects/…`（直す前）の
   2 通りで流し、AIP の METS に `dmdSec` が付くかどうかを比べる。
   これで 5 番の是正が効いているかが確定する。
5. **できた AIP の METS を、このアプリの AIP の METS と並べて読む。**
   保存用マスタの命名、`rightsMD` の有無、`fileGrp` の `USE` の値を突き合わせる。

実機で確かめたら、**本書の「限界」の節を書き換えること。**
確かめていないことを確かめたように書かないための節なので、
中身が変わったらここも変わらなければならない。

---

## 8. 再現方法

```
uv run pytest -q tests/test_interoperability.py
```

ずれの一覧を読みたいときは `-s` を付ける。

```
uv run pytest -q -s tests/test_interoperability.py -k gap_report
```

後方互換（新旧どちらの SIP も読めること）は別のファイルにある。

```
uv run pytest -q tests/test_sip_reader_compat.py
```

**是正した 7 件は assert になっている。** 出力が仕様から外れると落ちる。
残った 3 件は `KNOWN_GAPS` に「現状こうである」という形で入れてあるので、
**直したときにも落ちる。** どちらで落ちても、本書を更新する合図である。
