# AtoM の実機で取り込みを確かめる

`docs/interoperability.md` は、AtoM / Archivematica の**公式仕様との机上の突合**である。
「仕様の文面と出力が合っている」ことしか言えていない。

この文書は、それを**実機の裏付けに変える**ための手順である。

## なぜ優先度が高いか

ストアの掲載文と学会予稿に「AtoM が受け取れる形式」と書いてある。
いまその根拠は仕様の読み合わせだけである。**1 度でも実機に通れば、主張の質が変わる。**

最初にやるべきは `csv:check-import` である。**取り込まずに検査だけ**するタスクなので、
失敗しても壊れるものがない。ここだけで推定の大半に裏が取れる。

## 事前に用意するもの

- Docker Desktop（AtoM は MySQL 系・Elasticsearch・PHP を使うので数 GB 要る）
- このアプリで作った SIP 1 つ。中の
  `metadata/submissionDocumentation/atom-import.csv` が検査対象

## 1. AtoM を起動する

公式は compose 構成をリポジトリの `docker/` に置いている（2026-09-12 時点で確認）。

```
git clone https://github.com/artefactual/atom.git
cd atom
```

compose ファイルは `docker/docker-compose.dev.yml`。サービスは
`atom` / `atom_worker` / `nginx` / `elasticsearch` / `percona` / `memcached` / `gearmand`。

**Apple Silicon では `docker/docker-compose.override.arm.yml` を重ねる。**
既定の Elasticsearch は `elasticsearch-oss:7.10.2` で、arm64 版が無いためこの上書きがある。

起動の具体的なコマンドは公式の開発者マニュアル（Docker Compose の章）に従うこと。
**ここに書き写さないのは、版で変わるため。** リポジトリの `README.md` からリンクがある。

## 2. CSV を検査する（本命）

AtoM のタスク名はソースで確認済み（`lib/task/import/csvCheckImportTask.class.php`、
namespace `csv` / name `check-import`）。

```
docker compose exec atom php symfony csv:check-import --help
```

まず `--help` を見て、引数とオプションの形を確かめること。そのうえで、

```
docker compose exec atom php symfony csv:check-import /path/to/atom-import.csv
```

**見たいのは次の 3 点。**

1. 列名が認識されるか（`Unrecognized columns` に何も出ないこと）
2. `legacyId` / `parentId` による階層が成立しているか
3. 文字コードと改行の警告が出ないこと
   （`atom-import.csv` は BOM 無し・LF で出している。理由は `docs/interoperability.md`）

## 3. 実際に取り込む

検査が通ったら、ISAD(G) として取り込む。

```
docker compose exec atom php symfony import:bulk --help
```

取り込んだあと、ブラウザで次を確かめる。

- 全体の記述が 1 件、その下にファイル単位の記述が並ぶこと
- タイトル・識別子・日付・数量などが**空になっていない**こと
- `physicalCharacteristics` にフォーマット名と PRONOM ID が入っていること

## 4. 結果を持ち帰る

**`docs/interoperability.md` の「6. この検証の限界」を書き換えること。**
いまは「実機では確かめていない」と書いてある。確かめたら、いつ・どの版の AtoM で・
何が通って何が落ちたかに差し替える。

ずれが見つかったら `tests/test_interoperability.py` の `KNOWN_GAPS` に足す。
**文書とテストは 1 対 1 で対応させてある**（件数を assert している）ので、片方だけ直すと落ちる。

## Archivematica はどうするか

**後回しでよい。** サービス数が多く、AtoM より明らかに重い。

AtoM で列名の検査が通れば、記述メタデータ側の主張は裏が取れる。
Archivematica 側の主張（転送の構造、`metadata.csv`、`checksum.sha256` の位置）は、
仕様の文面が具体的で、突合の確度が比較的高い。優先順位としては AtoM が先。
