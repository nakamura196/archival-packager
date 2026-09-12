"""AtoM / Archivematica との相互運用の「主張」を、公式仕様と突き合わせて検証する。

このアプリは「出力は AtoM / Archivematica が読み取れる形に揃えてある」と説明して
いるが、**実際に読み込ませて確かめた記録が無い**。ここでは実機の代わりに、
公式ドキュメントに書かれた仕様と、実際にパイプラインが吐いたバイト列を
機械的に突き合わせる。

## このテストの立ち位置

初版（2026-09-12 午前）は「ずれを見つけても失敗させない」方針で、10 件のずれを
**現状こうであるという形で固定していた**。同日の午後、そのうち 7 件を直した
（1〜7）。直したものはここで**仕様に合っていることを assert する側へ移した**ので、
壊れたらこのテストが落ちる。

したがって、いまここでやっているのは次の 2 つ。

1. **仕様と合っているものを assert で固定する。** 固定しておかないと、
   後で誰かが壊したときに気づけない。
2. **まだずれているものを `KNOWN_GAPS` に残す。** 直ったらこのテストが落ちる。
   落ちたら `docs/interoperability.md` を更新する合図。

**列名とファイル配置を直すにあたって、公開済みのパッケージを読めなくしていない。**
`description.csv` の列見出しは人間向けラベルのまま変えず（`core/sip_reader.py` が
この見出しで読み戻している）、AtoM へ渡す機械名の CSV は `atom-import.csv` として
別に足した。新旧どちらの SIP も読めることは `tests/test_sip_reader_compat.py` で
別途固定してある。

突合の結果と出典は `docs/interoperability.md` に日本語でまとめてある。

## 仕様の出典（いずれも 2026-09-12 参照）

- AtoM ISAD(G) CSV インポートの列名（正本は AtoM 同梱の例 CSV）
  https://github.com/artefactual/atom/blob/qa/2.x/lib/task/import/example/isad/example_information_objects_isad.csv
- AtoM CSV import（2.8）
  https://www.accesstomemory.org/en/docs/2.8/user-manual/import-export/csv-import/
- AtoM CSV validation（2.8。未知列・BOM・legacyId の扱い）
  https://www.accesstomemory.org/en/docs/2.8/user-manual/import-export/csv-validation/
- Archivematica transfer（1.16）
  https://github.com/artefactual/archivematica-docs/blob/1.16/user-manual/transfer/transfer.rst
- Archivematica import metadata（1.16）
  https://github.com/artefactual/archivematica-docs/blob/1.16/user-manual/transfer/import-metadata.rst
- Archivematica AIP structure（1.16）
  https://www.archivematica.org/en/docs/archivematica-1.16/user-manual/archival-storage/aip-structure/
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import aip_pipeline, sip_pipeline
from archival_packager.core.aip_models import AIPOptions
from archival_packager.core.mets import METS_NS, PREMIS_NS
from archival_packager.core.models import SIPMetadata, SIPOptions
from archival_packager.core.spreadsheets import (
    ATOM_HEADERS,
    ATOM_IMPORT_HEADERS,
    ATOM_MACHINE_COLUMNS,
    METADATA_TEMPLATE_HEADERS,
)

NS = {"mets": METS_NS, "premis": PREMIS_NS}
UTF8_BOM = b"\xef\xbb\xbf"


# ==========================================================================
# 相手側の仕様（ハードコードの正本）
# ==========================================================================

#: AtoM 同梱の ISAD(G) インポート例 CSV のヘッダ行、そのまま 56 列。
#: **手で写さず、上記 URL の 1 行目をそのまま貼ってある。** ここを「それらしく」
#: 書き換えると、突合そのものが嘘になる。
ATOM_ISAD_CSV_COLUMNS: tuple[str, ...] = (
    "legacyId", "parentId", "qubitParentSlug", "accessionNumber", "identifier",
    "title", "levelOfDescription", "extentAndMedium", "repository",
    "archivalHistory", "acquisition", "scopeAndContent", "appraisal", "accruals",
    "arrangement", "accessConditions", "reproductionConditions", "language",
    "script", "languageNote", "physicalCharacteristics", "findingAids",
    "locationOfOriginals", "locationOfCopies", "relatedUnitsOfDescription",
    "publicationNote", "digitalObjectPath", "digitalObjectURI", "generalNote",
    "subjectAccessPoints", "placeAccessPoints", "nameAccessPoints",
    "genreAccessPoints", "descriptionIdentifier", "institutionIdentifier",
    "rules", "descriptionStatus", "levelOfDetail", "revisionHistory",
    "languageOfDescription", "scriptOfDescription", "sources", "archivistNote",
    "publicationStatus", "physicalObjectName", "physicalObjectLocation",
    "physicalObjectType", "alternativeIdentifiers", "alternativeIdentifierLabels",
    "eventDates", "eventTypes", "eventStartDates", "eventEndDates", "eventActors",
    "eventActorHistories", "culture",
)

#: こちらの列 → AtoM の列（意味の対応）。
#: **名前ではなく意味で対応づけている。** 名前は 1 つも一致しないので、
#: 名前だけ見ると「何も対応していない」に見えるが、実際には中身は移せる。
#: 対応先は AtoM 2.8 の ISAD(G) データ入力テンプレート
#: https://www.accesstomemory.org/en/docs/2.8/user-manual/data-templates/isad-template/
OUR_COLUMN_TO_ATOM: dict[str, str] = {
    "Parent ID": "parentId",
    "Identifier": "identifier",
    "Title": "title",
    "Archive Creator": "eventActors",
    "Date expression": "eventDates",
    "Date start": "eventStartDates",
    "Date end": "eventEndDates",
    "Level of description": "levelOfDescription",
    "Extent and medium": "extentAndMedium",
    "Scope and content": "scopeAndContent",
    "Arrangement (optional)": "arrangement",
    "Accession number": "accessionNumber",
    "Appraisal, destruction, and scheduling information (optional)": "appraisal",
    "Name access points (optional)": "nameAccessPoints",
    "Geographic access points (optional)": "placeAccessPoints",
    "Conditions governing access (optional)": "accessConditions",
    "Conditions governing reproduction (optional)": "reproductionConditions",
    "Language of material (optional)": "language",
    "Physical characteristics & technical requirements affecting use (optional)":
        "physicalCharacteristics",
    "Finding aids (optional)": "findingAids",
    "Related units of description (optional)": "relatedUnitsOfDescription",
    "Archival history (optional)": "archivalHistory",
    "Immediate source of acquisition or transfer (optional)": "acquisition",
    "Archivists' note (optional)": "archivistNote",
    "General note (optional)": "generalNote",
    "Description status": "descriptionStatus",
}

#: Archivematica が metadata.csv に求める先頭列名。
#: 「The filename column must come first and the filename path must always
#: start with ``objects/``」（import-metadata.rst）。
AM_METADATA_FIRST_COLUMN = "filename"
AM_METADATA_PATH_PREFIX = "objects/"

#: import-metadata.rst が示す filename の形は 2 通りだけ。
#: ファイル ``objects/beihai.tif`` と、ディレクトリ ``objects/CoastNews-1964-01-02``。
#: **末尾スラッシュだけの ``objects/`` という書き方は例に無い。**
AM_METADATA_WHOLE_ROW = "objects"

#: bag として渡す場合、Archivematica は filename が ``data`` で始まることを求める。
#: 「Material that you add to a bag will always be inside the payload directory,
#: so the filename path must always begin with ``data``」（import-metadata.rst）。
AM_BAG_METADATA_PATH_PREFIX = "data"

#: Archivematica が転送で予約している名前（transfer.rst）。
AM_RESERVED_DIRS = ("objects", "metadata", "logs")

#: 外部で作ったチェックサムを Archivematica に検証させる場合の置き場と名前。
#: 「Checksum files are placed in the ``metadata`` directory」（transfer.rst）。
AM_CHECKSUM_NAMES = ("checksum.md5", "checksum.sha1", "checksum.sha256", "checksum.sha512")


# ==========================================================================
# 実物を作る
# ==========================================================================


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    """突合用の入力ツリー。日本語名とサブフォルダを 1 つずつ含める。

    相互運用で最初に壊れるのは非 ASCII の名前とパス区切りなので、
    ASCII だけの入力で確かめても意味が薄い。
    """
    src = tmp_path_factory.mktemp("interop-in")
    (src / "文書").mkdir()
    (src / "a.txt").write_text("資料 A\n", encoding="utf-8")
    (src / "文書" / "b.txt").write_text("資料 B\n", encoding="utf-8")
    return src


def _run_sip(source: Path, out_parent: Path, **opts) -> Path:
    result = sip_pipeline.run(
        input_path=source,
        output_parent=out_parent,
        metadata=SIPMetadata(
            identifier="2026-移管-総務課",
            title="総務課文書",
            scope_note="相互運用の突合用",
            date_note="2024–2025",
        ),
        options=SIPOptions(**opts),
        progress=lambda _m: None,
    )
    return result.sip_path


@pytest.fixture(scope="module")
def plain_sip(source: Path, tmp_path_factory) -> Path:
    """非 bag の SIP。Archivematica の standard transfer と突き合わせる。"""
    return _run_sip(source, tmp_path_factory.mktemp("interop-sip"))


@pytest.fixture(scope="module")
def bagged_sip(source: Path, tmp_path_factory) -> Path:
    """BagIt bag の SIP。Archivematica の unzipped bag transfer と突き合わせる。"""
    return _run_sip(source, tmp_path_factory.mktemp("interop-bag"), make_bag=True)


@pytest.fixture(scope="module")
def aip(plain_sip: Path, tmp_path_factory):
    """SIP から AIP まで通したもの。Archivematica の AIP 構造と突き合わせる。

    正規化は OFF。外部変換ツール（Ghostscript 等）の有無で構造が変わると、
    環境によって突合結果が変わってしまう。
    """
    out = tmp_path_factory.mktemp("interop-aip")
    return aip_pipeline.run(
        sip_root=plain_sip,
        output_parent=out,
        options=AIPOptions(normalize=False),
        progress=lambda _m: None,
    )


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """BOM を剥がして CSV を読み、(ヘッダ, データ行) を返す。"""
    text = path.read_text(encoding="utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    return rows[0], rows[1:]


# ==========================================================================
# 1. description.csv と AtoM の ISAD(G) CSV
# ==========================================================================


class TestAtomDescriptionColumns:
    def test_column_names_are_frozen(self, plain_sip: Path):
        """出している列を固定する。

        AtoM に合わせて直すにせよ直さないにせよ、**黙って変わることだけは困る**。
        過去に作った SIP を読み戻す `sip_reader` がこの見出しを見ている。
        """
        header, _ = _read_csv(plain_sip / "metadata" / "submissionDocumentation" / "description.csv")
        assert tuple(header) == ATOM_HEADERS
        assert len(header) == 26

    def test_description_csv_deliberately_keeps_human_labels(self, plain_sip: Path):
        """**description.csv の列名は、意図して AtoM の機械名に合わせていない。**

        これは担当者が手で書き込むシートで、`core/sip_reader.py` がこの見出しで
        過去のパッケージを読み戻している。機械名にすると、人が読めなくなるうえ
        公開済みのアプリで作った SIP から AIP を作れなくなる。

        AtoM に渡す分は atom-import.csv に分けた（下の TestAtomImportCsv）。
        ここで固定するのは「分けた状態のまま保つ」ことで、
        うっかり片方に寄せられていないかを見ている。
        """
        header, _ = _read_csv(plain_sip / "metadata" / "submissionDocumentation" / "description.csv")
        matched = [h for h in header if h in ATOM_ISAD_CSV_COLUMNS]
        assert matched == [], "description.csv は人間向けラベルのまま（sip_reader が読み戻す）"

    def test_every_our_column_has_an_atom_counterpart(self):
        """対応表そのものの健全性を見る。

        「意味の対応先がある」と書いておきながら、その名前が AtoM 側に
        存在しなければ表が嘘になる。写し間違いをここで捕まえる。
        """
        assert set(OUR_COLUMN_TO_ATOM) == set(ATOM_HEADERS)
        unknown = sorted(v for v in OUR_COLUMN_TO_ATOM.values() if v not in ATOM_ISAD_CSV_COLUMNS)
        assert unknown == []
        # 1 つの AtoM 列に 2 つ割り当てていないこと。
        assert len(set(OUR_COLUMN_TO_ATOM.values())) == 26

    def test_atom_columns_we_never_emit(self):
        """AtoM 側にあって、こちらが一切出していない列を固定する（29 列）。

        件数を固定するのは、後から列を足したときに「何が埋まるようになったか」を
        レビューで見えるようにするため。2026-09-12 に legacyId を出すようにして
        30 列から 29 列になった。

        `culture`（記述そのものの言語）は**まだ出していない**。無いと AtoM の
        既定の言語で取り込まれるので、日本語の記述を英語サイトに入れると
        `ja` が付かない。入力欄が無いため今回は見送った（KNOWN_GAPS に残してある）。
        """
        emitted = set(ATOM_IMPORT_HEADERS)
        missing = [c for c in ATOM_ISAD_CSV_COLUMNS if c not in emitted]
        assert len(missing) == 29
        assert "legacyId" not in missing, "再インポート時の突合キーは出している"
        for still_missing in ("culture", "qubitParentSlug", "publicationStatus", "repository"):
            assert still_missing in missing

    def test_single_row_describes_the_whole_sip(self, plain_sip: Path):
        """人が書くシートは SIP 全体で 1 行のまま。

        担当者に書かせるのは移管 1 件ぶんの記述で、ファイルごとに書かせるもの
        ではない。AtoM に渡す階層（全体 → ファイル）は atom-import.csv 側で作る。
        """
        header, rows = _read_csv(plain_sip / "metadata" / "submissionDocumentation" / "description.csv")
        assert len(rows) == 1
        values = dict(zip(header, rows[0], strict=True))
        assert values["Title"] == "総務課文書"
        assert values["Identifier"] == "2026-移管-総務課"
        # AtoM の levelOfDescription タクソノミーに実在する語であること。
        assert values["Level of description"] == "File"

    def test_csv_is_utf8_with_a_bom(self, plain_sip: Path):
        """BOM 付き UTF-8。**AtoM 側の仕様には反していない。**

        AtoM の CSV validation が ERROR にするのは
        「This file includes a Unicode BOM, but it is not UTF-8」の場合だけで、
        UTF-8 の BOM は対象外。Excel に読ませるために BOM を付けている
        こちらの都合（sip_builder._write_csv）と両立する。
        """
        raw = (plain_sip / "metadata" / "submissionDocumentation" / "description.csv").read_bytes()
        assert raw.startswith(UTF8_BOM)
        assert raw[len(UTF8_BOM):].decode("utf-8").startswith("Parent ID,")

    def test_line_endings_are_crlf(self, plain_sip: Path):
        """行末は CRLF のみ。RFC 4180 どおりで、AtoM 側も特に禁じていない。

        裸の LF が混ざっていないことまで見る。混在すると、行末を自前で切る
        実装（AtoM の CSV validation は php の fgetcsv を使う）で列がずれる。
        """
        raw = (plain_sip / "metadata" / "submissionDocumentation" / "description.csv").read_bytes()
        assert raw.endswith(b"\r\n")
        assert raw.count(b"\n") == raw.count(b"\r\n") == 2  # ヘッダ + データ 1 行


class TestAtomImportCsv:
    """AtoM に渡す CSV（2026-09-12 に追加。ずれ 1・2・3 の是正）。

    description.csv と違い、**これは人向けではない。** AtoM の csv:import に
    そのまま流せることだけを目的にしている。
    """

    @staticmethod
    def _path(plain_sip: Path) -> Path:
        return plain_sip / "metadata" / "submissionDocumentation" / "atom-import.csv"

    def test_every_column_name_exists_in_atom(self, plain_sip: Path):
        """**列名が全部 AtoM に実在すること。** ずれ 1 の核心。

        未知の列は「Unrecognized columns will be ignored by AtoM when the CSV is
        imported」＝エラーにならずに捨てられる。1 列でも綴りを間違えると、
        その列の記述だけが黙って消える。
        """
        header, _ = _read_csv(self._path(plain_sip))
        assert tuple(header) == ATOM_IMPORT_HEADERS
        unknown = [h for h in header if h not in ATOM_ISAD_CSV_COLUMNS]
        assert unknown == [], f"AtoM に無い列名: {unknown}"

    def test_the_mapping_table_matches_the_implementation(self):
        """本書 2.2 の対応表と、実装の並びが一致していること。

        対応表（文書）と実装が別々に動くと、文書が嘘になる。位置で対応づけて
        いるので、並びがずれると値が別の列に入る（気づきにくい壊れ方をする）。
        """
        assert dict(zip(ATOM_HEADERS, ATOM_MACHINE_COLUMNS, strict=True)) == OUR_COLUMN_TO_ATOM

    def test_legacy_id_is_present_and_unique(self, plain_sip: Path):
        """ずれ 2。legacyId が無いと、入れ直したとき既存レコードと紐づかない。

        CsvLegacyIdValidator は欠落時に「Future CSV updates may not match these
        records」と警告する。また同じ culture で legacyId が重複すると ERROR に
        なるので、一意であることも見る。
        """
        header, rows = _read_csv(self._path(plain_sip))
        assert header[0] == "legacyId"
        ids = [r[0] for r in rows]
        assert all(ids), "空の legacyId があってはいけない"
        assert len(set(ids)) == len(ids)

    def test_hierarchy_is_expressed_with_parent_id(self, plain_sip: Path):
        """ずれ 3。SIP 全体を親に、ファイル 1 件ずつを子として並べる。

        「if your CSV is not properly ordered with parent records appearing
        before their children, your import will fail」（csv-import）ので、
        親が上にあることまで見る。
        """
        header, rows = _read_csv(self._path(plain_sip))
        i_parent = header.index("parentId")
        i_level = header.index("levelOfDescription")

        whole, items = rows[0], rows[1:]
        assert whole[i_parent] == "", "全体行に親は無い"
        assert whole[i_level] == "File"
        assert len(items) == 2, "入力の 2 ファイルぶん"
        for row in items:
            assert row[i_parent] == whole[0]
            assert row[i_level] == "Item"

        ids = [r[0] for r in rows]
        assert all(ids.index(r[i_parent]) < ids.index(r[0]) for r in items), "親が先に来ること"

    def test_item_rows_point_at_real_payload_files(self, plain_sip: Path):
        """目録の各行が、実際に転送に入っているファイルを指していること。"""
        header, rows = _read_csv(self._path(plain_sip))
        i_identifier = header.index("identifier")
        for row in rows[1:]:
            assert (plain_sip / "objects" / row[i_identifier]).is_file(), row[i_identifier]

    def test_unix_line_breaks_and_no_bom(self, plain_sip: Path):
        """**AtoM 向けだけは BOM 無し・LF。** description.csv（Excel 向け）と逆。

        - 改行: 「AtoM's CSV import will expect Unix-style line breaks (``\\n``)」。
          CRLF は CSV validation が「unintended blank rows」の原因として名指しする。
        - BOM: UTF-8 の BOM は ERROR にならないが、**剥がすとは書かれていない**。
          残ったまま読まれると先頭の列名が legacyId と認識されず、未知の列として
          捨てられる。それでは legacyId を出した意味が無い。
        """
        raw = self._path(plain_sip).read_bytes()
        assert not raw.startswith(UTF8_BOM)
        assert b"\r" not in raw
        assert raw.decode("utf-8").startswith("legacyId,")


# ==========================================================================
# 2. Archivematica の standard transfer としての SIP
# ==========================================================================


class TestArchivematicaStandardTransfer:
    def test_top_level_has_objects_and_metadata(self, plain_sip: Path):
        """予約名の使い方が仕様どおりであること。

        transfer.rst は zipped/standard transfer について
        「including an ``objects`` and ``metadata`` directory including
        Archivematica's special files such as metadata.csv」と書いており、
        この 2 つを直下に置く形を明示的に認めている。
        """
        assert (plain_sip / "objects").is_dir()
        assert (plain_sip / "metadata").is_dir()
        # 予約名を別の用途に使っていないこと（metadata は「must not be used
        # for anything else」と明記されている）。
        extra = {p.name for p in plain_sip.iterdir() if p.is_dir()} - set(AM_RESERVED_DIRS)
        assert extra == set(), f"予約名以外のトップレベルディレクトリ: {extra}"

    def test_payload_keeps_its_relative_paths_under_objects(self, plain_sip: Path):
        assert (plain_sip / "objects" / "a.txt").is_file()
        assert (plain_sip / "objects" / "文書" / "b.txt").is_file()

    def test_submission_documentation_is_nested_in_metadata(self, plain_sip: Path):
        """「Inside the metadata directory, a nested directory called
        ``submissionDocumentation``」（transfer.rst）どおりの位置。
        """
        subdoc = plain_sip / "metadata" / "submissionDocumentation"
        assert subdoc.is_dir()
        for name in ("description.csv", "atom-import.csv", "formats.csv",
                     "accession.csv", "report.txt"):
            assert (subdoc / name).is_file(), name

    def test_metadata_csv_sits_directly_under_metadata(self, plain_sip: Path):
        """metadata.csv は metadata/ 直下（submissionDocumentation/ ではない）。"""
        assert (plain_sip / "metadata" / "metadata.csv").is_file()
        assert not (plain_sip / "metadata" / "submissionDocumentation" / "metadata.csv").exists()

    def test_metadata_csv_first_column_is_filename(self, plain_sip: Path):
        """先頭列が filename であること。Archivematica の唯一の構造要件。"""
        header, _ = _read_csv(plain_sip / "metadata" / "metadata.csv")
        assert header[0] == AM_METADATA_FIRST_COLUMN
        assert tuple(header) == METADATA_TEMPLATE_HEADERS

    def test_metadata_csv_other_columns_are_dublin_core(self, plain_sip: Path):
        """2 列目以降がすべて dc. / dcterms. 形式であること。

        そうでない列は「written into a separate ``<dmdSec>`` as
        ``MDTYPE="OTHER"``」となり、AtoM / ArchivesSpace へは渡らない。
        """
        header, _ = _read_csv(plain_sip / "metadata" / "metadata.csv")
        non_dc = [c for c in header[1:] if not c.startswith(("dc.", "dcterms."))]
        assert non_dc == []

    def test_metadata_csv_file_rows_start_with_objects(self, plain_sip: Path):
        """ファイル行のパスが objects/ で始まること（仕様どおり）。"""
        _, rows = _read_csv(plain_sip / "metadata" / "metadata.csv")
        file_rows = [r[0] for r in rows if r[0] != AM_METADATA_WHOLE_ROW]
        assert file_rows == ["objects/a.txt", "objects/文書/b.txt"]
        assert all(p.startswith(AM_METADATA_PATH_PREFIX) for p in file_rows)

    def test_metadata_csv_whole_transfer_row_names_the_objects_directory(self, plain_sip: Path):
        """全体行は "objects"（末尾スラッシュ無し）。2026-09-12 に是正（ずれ 4）。

        ドキュメントが示すのはファイル（``objects/audio/bird.mp3``）と
        ディレクトリ（``objects/CoastNews-1964-01-02``）の 2 通りで、
        末尾スラッシュだけの形は例に無い。転送内に "objects/" という名前の実体は
        存在しないため、対応先が見つからず行ごと落ちる可能性があった。
        ディレクトリを指す形に合わせ、実在する objects ディレクトリを名指しする。
        """
        _, rows = _read_csv(plain_sip / "metadata" / "metadata.csv")
        assert rows[0][0] == AM_METADATA_WHOLE_ROW
        assert not rows[0][0].endswith("/")
        assert (plain_sip / rows[0][0]).is_dir(), "転送内に実在する名前であること"

    def test_checksum_file_is_where_archivematica_looks(self, plain_sip: Path):
        """checksum.sha256 の置き場と行の書式。2026-09-12 に是正（ずれ 6・7）。

        transfer.rst は「Checksum files are placed in the ``metadata``
        directory」とし、行は「the checksum, followed by two spaces, followed by
        the file path」で、示されている例（``beihai.tif`` /
        ``subdirectory/piiTestDataCreditCardNumbers.txt``）のパスに
        ``objects/`` の接頭辞は無い＝パスは objects/ からの相対。
        """
        manifest = plain_sip / "metadata" / "checksum.sha256"
        assert manifest.is_file()
        assert manifest.name in AM_CHECKSUM_NAMES

        lines = manifest.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line in lines:
            digest, sep, path = line.partition("  ")
            assert sep == "  ", "区切りは空白 2 個"
            assert len(digest) == 64
            assert not path.startswith("objects/")
            assert (plain_sip / "objects" / path).is_file(), path

    def test_the_internal_fixity_manifest_is_still_there(self, plain_sip: Path):
        """**同じ内容が 2 箇所にある。読み手が 2 人いるため。**

        `metadata/checksum.sha256` は Archivematica 用（objects/ からの相対）。
        `metadata/submissionDocumentation/checksum.sha256` はこのアプリの
        `core/fixity.py` 用（SIP ルート基準なので objects/ が付く）。
        後者を消すと、このアプリ自身の完全性確認が
        「マニフェストが見つかりません」で素通りになる。
        """
        ours = plain_sip / "metadata" / "submissionDocumentation" / "checksum.sha256"
        assert ours.is_file()
        assert all(
            line.partition("  ")[2].startswith("objects/")
            for line in ours.read_text(encoding="utf-8").splitlines()
        )


# ==========================================================================
# 3. Archivematica の unzipped bag transfer としての SIP
# ==========================================================================


class TestArchivematicaBagTransfer:
    def test_bag_is_valid_per_the_bagit_spec(self, bagged_sip: Path):
        """RFC 8493 準拠であること。Archivematica は転送の早い段階で bag を検証する。"""
        from archival_packager.core import sip_builder

        sip_builder.validate_bag(bagged_sip)  # 失敗すれば例外

    def test_bag_root_has_the_required_tag_files(self, bagged_sip: Path):
        for name in ("bagit.txt", "bag-info.txt", "manifest-sha256.txt", "tagmanifest-sha256.txt"):
            assert (bagged_sip / name).is_file(), name
        assert (bagged_sip / "data").is_dir()

    def test_bagit_declaration_is_well_formed(self, bagged_sip: Path):
        text = (bagged_sip / "bagit.txt").read_text(encoding="utf-8")
        assert "BagIt-Version: 0.97" in text
        assert "Tag-File-Character-Encoding: UTF-8" in text

    def test_manifest_lists_payload_relative_to_data(self, bagged_sip: Path):
        """マニフェストのパスは data/ からの相対（BagIt 仕様どおり）。"""
        lines = (bagged_sip / "manifest-sha256.txt").read_text(encoding="utf-8").splitlines()
        paths = sorted(line.split(maxsplit=1)[1] for line in lines if line.strip())
        assert "data/objects/a.txt" in paths
        assert "data/objects/文書/b.txt" in paths
        assert all(p.startswith("data/") for p in paths)

    def test_payload_layout_matches_the_documented_bag_transfer(self, bagged_sip: Path):
        """metadata と submissionDocumentation がペイロード内にあること。

        「Submission documentation can also be added to a bag using the above
        method. The ``submissionDocumentation`` directory should be nested inside
        the metadata directory」（import-metadata.rst）。
        """
        assert (bagged_sip / "data" / "objects" / "a.txt").is_file()
        assert (bagged_sip / "data" / "metadata" / "metadata.csv").is_file()
        assert (bagged_sip / "data" / "metadata" / "submissionDocumentation").is_dir()

    def test_bag_metadata_csv_paths_begin_with_data(self, bagged_sip: Path):
        """bag のとき filename は ``data`` で始まる。2026-09-12 に是正（ずれ 5）。

        「the filename path must always begin with ``data``」（import-metadata.rst）。
        bag のペイロードは必ず data/ の中にあるので、``objects/a.txt`` という行に
        対応する実体は転送内に存在しない。直す前は bag / 非 bag で同じ文字列を
        書いており、**bag として渡すと file 単位の記述メタデータが
        どのオブジェクトにも紐づかなかった。**
        """
        _, rows = _read_csv(bagged_sip / "data" / "metadata" / "metadata.csv")
        paths = [r[0] for r in rows]
        assert paths == ["data/objects", "data/objects/a.txt", "data/objects/文書/b.txt"]
        assert all(p.startswith(AM_BAG_METADATA_PATH_PREFIX) for p in paths)
        # 記載されたパスが bag の中に実在すること。ここが肝で、
        # 実在しない限り記述はどのファイルにも紐づかない。
        for p in paths:
            assert (bagged_sip / p).exists(), p


# ==========================================================================
# 4. Archivematica 風 AIP
# ==========================================================================


class TestArchivematicaAipShape:
    def test_aip_is_a_bag_with_data_payload(self, aip):
        for name in ("bagit.txt", "bag-info.txt", "manifest-sha256.txt"):
            assert (aip.aip_path / name).is_file(), name
        assert (aip.aip_path / "data").is_dir()

    def test_mets_is_named_after_the_aip_uuid_at_the_data_root(self, aip):
        """「METS.uuid.xml」（aip-structure）どおりの名前と位置。"""
        assert aip.mets_path == aip.aip_path / "data" / f"METS.{aip.aip_uuid}.xml"
        assert aip.mets_path.is_file()

    def test_data_has_objects_and_logs(self, aip):
        assert (aip.aip_path / "data" / "objects" / "a.txt").is_file()
        assert (aip.aip_path / "data" / "logs").is_dir()

    def test_submission_documentation_lives_under_objects(self, aip):
        """AIP では submissionDocumentation が objects/ の下に移る。

        Archivematica の AIP でも「objects/: … plus ``metadata/transfers/``
        and ``submissionDocumentation/`` folders」とされており、位置は一致する。
        """
        subdoc = aip.aip_path / "data" / "objects" / "submissionDocumentation"
        assert subdoc.is_dir()
        assert (subdoc / "description.csv").is_file()

    def test_readme_html_sits_at_the_data_root(self, aip):
        """Archivematica の AIP と同じく data/README.html を置くこと。

        以前は data/logs/README.txt しか無く、突合表の Gap 9 として残っていた。
        人向けの案内が無いと、**10 年後にこの bag を渡された人が、まず何を
        見ればよいかを知る手立てが METS（XML）しかない。**
        """
        readme = aip.aip_path / "data" / "README.html"
        assert readme.is_file()
        assert (aip.aip_path / "data" / "logs" / "README.txt").is_file()

    def test_readme_html_is_in_the_payload_manifest(self, aip):
        """**payload に入れた以上、マニフェストに載っていること。**

        BagIt では data/ 配下は payload であり、manifest に無いファイルが
        あると bag として不正になる（検証が落ちる）。`bagit.make_bag` を
        呼ぶ前に書いているかどうかで決まるので、書く場所を動かすと静かに
        壊れる。ここで固定しておく。
        """
        manifest = (aip.aip_path / "manifest-sha256.txt").read_text(encoding="utf-8")
        assert "data/README.html" in manifest

    def test_readme_html_explains_itself_in_japanese_and_english(self, aip):
        """**受け取る人の言語はこちらから決められない。**

        画面表示の言語はアプリの設定で変わるが、パッケージは作った環境から
        切り離されて流通する。両方の言語で書いておく。
        """
        text = (aip.aip_path / "data" / "README.html").read_text(encoding="utf-8")
        assert "このパッケージについて" in text
        assert "About this package" in text
        # METS の実ファイル名を案内すること（UUID が埋まっていないと迷子になる）。
        assert f"METS.{aip.aip_uuid}.xml" in text
        # 読み戻し確認を形式適合性検査と読み違えられては困る。
        assert "veraPDF" in text and "JHOVE" in text

    def test_readme_html_references_nothing_outside_the_package(self, aip):
        """外部の CSS・画像・スクリプトを読みに行かないこと。

        ネットワークの無い場所で、ブラウザに放り込んだだけで読める必要がある。
        参照先はいずれ必ず消える（消えた時点で、読めないページだけが残る）。
        """
        text = (aip.aip_path / "data" / "README.html").read_text(encoding="utf-8")
        assert "<script" not in text
        assert 'src=' not in text
        assert '<link' not in text

    def test_tag_manifest_uses_sha256_not_md5(self, aip):
        """**ずれ（小）。** 公式 AIP の例は tagmanifest-md5.txt。

        BagIt 仕様上どちらも適法で、Archivematica 側が md5 を要求しているわけ
        ではない。SHA-256 に揃えているのは意図した選択（sip_builder の冒頭注）。
        """
        assert (aip.aip_path / "tagmanifest-sha256.txt").is_file()
        assert not (aip.aip_path / "tagmanifest-md5.txt").exists()

    def test_mets_has_the_four_expected_sections(self, aip):
        """dmdSec / amdSec / fileSec / structMap が揃っていること。"""
        root = etree.parse(str(aip.mets_path)).getroot()
        assert root.tag == f"{{{METS_NS}}}mets"
        for section in ("dmdSec", "amdSec", "fileSec", "structMap"):
            assert root.findall(f"mets:{section}", NS), section

    def test_mets_carries_premis_objects_events_and_agents(self, aip):
        """PREMIS の 3 実体が入っていること。

        Archivematica の AIP METS は「links digital objects to their descriptive,
        technical, provenance, and rights metadata」とされる。provenance の実体が
        PREMIS の object / event / agent なので、そこだけ機械的に確かめる。
        """
        root = etree.parse(str(aip.mets_path)).getroot()
        for tag in ("object", "event", "agent"):
            assert root.findall(f".//premis:{tag}", NS), tag

    def test_structmap_is_physical(self, aip):
        root = etree.parse(str(aip.mets_path)).getroot()
        types = [sm.get("TYPE") for sm in root.findall("mets:structMap", NS)]
        assert "physical" in types

    def test_no_rights_metadata_is_produced(self, aip):
        """**ずれ。** PREMIS rights を出していない。

        Archivematica は転送の metadata/rights.csv から PREMIS rights を作る。
        こちらは rights.csv を作らず、METS にも rightsMD を出さない。
        利用条件を AIP から読み取ることはできない。
        """
        assert not (aip.aip_path / "data" / "metadata" / "rights.csv").exists()
        root = etree.parse(str(aip.mets_path)).getroot()
        assert root.findall(".//mets:rightsMD", NS) == []


# ==========================================================================
# 5. ずれの一覧（文書と数を突き合わせる）
# ==========================================================================


@dataclass(frozen=True)
class Gap:
    """仕様と実際の出力の食い違い 1 件。"""

    area: str
    ours: str
    theirs: str
    impact: str


#: `docs/interoperability.md` の突合表と 1 対 1 で対応させる。
#: **ここを増やしたら文書も直す。** 片方だけ更新されると、
#: 「検証した」という主張がまた実態から離れる。
#:
#: 初版の 10 件のうち 1〜7 は 2026-09-12 に是正し、この一覧から外した
#: （それぞれ上の assert へ移してある）。11 はその作業中に見つけたもの。
KNOWN_GAPS: tuple[Gap, ...] = (
    Gap(
        area="Archivematica: PREMIS rights",
        ours="rights.csv を作らず rightsMD も出さない",
        theirs="metadata/rights.csv から PREMIS rights を生成",
        impact="中。利用条件が AIP から機械的に読めない。権利情報の入力画面から要る",
    ),
    Gap(
        area="AIP: tagmanifest のアルゴリズム",
        ours="tagmanifest-sha256.txt",
        theirs="公式例は tagmanifest-md5.txt",
        impact="なし。BagIt 仕様上どちらも適法。直さない",
    ),
    Gap(
        area="AtoM: culture 列",
        ours="出していない",
        theirs="記述そのものの言語（ja / en …）",
        impact="小。AtoM の既定の言語で取り込まれる。記述言語の入力欄から要る",
    ),
)


def test_known_gap_count_is_frozen():
    """ずれの件数を固定する。

    直したときも、新しく見つけたときも、ここが落ちる。落ちたら
    `docs/interoperability.md` の突合表を必ず一緒に直す。

    2026-09-12: 10 件 → 7 件を是正 → 新たに 1 件（culture）を記録して 4 件。
    2026-09-12: data/README.html（Gap 9）を是正して 3 件。
    """
    assert len(KNOWN_GAPS) == 3
    assert len({g.area for g in KNOWN_GAPS}) == 3


def test_gap_report_is_printable(capsys):
    """ずれの一覧を人が読める形で出す（`pytest -s` で見える）。

    assert で落とすのが目的ではない。**「何が合っていないか」を
    テストの副産物としていつでも取り出せる**ようにしておく。
    """
    for g in KNOWN_GAPS:
        print(f"[{g.area}]\n  こちら: {g.ours}\n  相手  : {g.theirs}\n  影響  : {g.impact}")
    assert "こちら" in capsys.readouterr().out
