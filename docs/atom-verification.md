---
title: "AtoM の実機で取り込みを確かめる"
eyebrow: Archival Packager 技術資料
---

`docs/interoperability.md` は、AtoM / Archivematica の**公式仕様との机上の突合**である。
「仕様の文面と出力が合っている」ことしか言えていない。

この文書は、それを**実機の裏付けに変える**ための手順である。

**2026年10月28日に、この手順で AtoM 2.10.2 に通した。** 結果は
`docs/interoperability.md` の「6.1 AtoM で確かめたこと」にある。
版を変えて確かめ直すときも、この手順を使う。

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

**`docs/interoperability.md` の 6 節（実機で確かめたことと、限界）を書き換えること。**
いつ・どの版の AtoM で・何が通って何が落ちたかを書く。

ずれが見つかったら `tests/test_interoperability.py` の `KNOWN_GAPS` に足す。
**文書とテストは 1 対 1 で対応させてある**（件数を assert している）ので、片方だけ直すと落ちる。

## 10月28日に実際に打ったコマンド

compose の外から打つ場合の形。コンテナ名は `docker ps` で確かめること
（`docker/` で起動すると `docker-atom-1` になる）。CSV はコンテナの中に複写してから渡す。

```
docker exec docker-atom-1 mkdir -p /tmp/ap
docker cp atom-import.csv docker-atom-1:/tmp/ap/
docker exec -w /atom/src docker-atom-1 php symfony csv:check-import -i /tmp/ap/atom-import.csv
docker exec -w /atom/src docker-atom-1 php symfony csv:import --source-name=ap-issue2 --index --no-confirmation /tmp/ap/atom-import.csv
docker exec -w /atom/src docker-atom-1 php symfony csv:import --source-name=ap-issue2 --update=match-and-update --index --no-confirmation /tmp/ap/atom-import.csv
```

- **入れ直しの確かめには `--source-name` を揃えること。** AtoM は `legacyId` を
  取り込み元の名前ごとに覚えているので、名前が違うと別物として新しく作る。
  指定しないとファイル名（`atom-import.csv`）が使われる
- `--update` も `--skip-matched` も付けずに流すと、照合をせずに新しく作る
  （`lib/QubitFlatfileImport.class.php` の既定の動きとして書かれている。実機では流していない）
- 取り込んだ記述は「下書き」になるので、ログインしないと画面で見えない（HTTP 403）。
  中身はデータベースの `keymap`（`source_name` で絞る）から `information_object` を引いて確かめた

## Archivematica はどうするか

**後回しでよい。** サービス数が多く、AtoM より明らかに重い。

AtoM で列名の検査が通れば、記述メタデータ側の主張は裏が取れる。
Archivematica 側の主張（転送の構造、`metadata.csv`、`checksum.sha256` の位置）は、
仕様の文面が具体的で、突合の確度が比較的高い。優先順位としては AtoM が先。
