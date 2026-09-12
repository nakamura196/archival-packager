"""旧形式の SIP も新形式の SIP も読めることを固定する。

このアプリは公開済みで、利用者の手元には **旧形式のパッケージが既にある。**
2026-09-12 の相互運用の是正で、Archivematica 側に合わせて次の 2 つを変えた。

    checksum.sha256   旧 metadata/submissionDocumentation/ に "<hash>  objects/<rel>"
                      新 metadata/ 直下に "<hash>  <rel>"
    metadata.csv      旧 filename が "objects/"（全体行）と "objects/<rel>"
                      新 filename が "objects" と "objects/<rel>"、
                         bag では "data/objects" と "data/objects/<rel>"

**読めなくなると、過去に作った SIP から AIP を作れなくなる。** これは
「相手の仕様に合わせる」ことより優先される制約なので、ここで両方を固定する。

ここで見るのは `core/sip_reader.py` の継承（ハッシュと記述メタデータ）である。
継承できないと AIP 段で SHA-256 を再計算することになり、
「受入時の記録」と「保存時の記録」を突き合わせる根拠が失われる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archival_packager.core import sip_pipeline, sip_reader
from archival_packager.core.checksums import sha256_of
from archival_packager.core.models import SIPMetadata, SIPOptions

PAYLOAD = {"a.txt": "資料 A\n", "文書/b.txt": "資料 B\n"}

FORMATS_HEADER = (
    "相対パス,フォーマット名,PRONOM,MIME,拡張子警告,サイズ(バイト),更新日時,SHA-256,ウイルス検査"
)


def _write(path: Path, text: str, *, bom: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((("﻿" if bom else "") + text).encode("utf-8"))


def _make_payload(objects: Path) -> dict[str, str]:
    """objects/ 配下に資料を置き、相対パス -> SHA-256 を返す。"""
    digests: dict[str, str] = {}
    for rel, body in PAYLOAD.items():
        target = objects / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        digests[rel] = sha256_of(target)
    return digests


def make_legacy_sip(root: Path, *, bagged: bool = False) -> Path:
    """0.1.x のアプリが作っていた形の SIP を手で組み立てる。

    **実物の旧版を呼べないので、旧版が書いていたバイト列をここに写している。**
    元にしたのは 0.1.x の sip_builder（checksum は objects/ 付きで
    submissionDocumentation 配下）と spreadsheets（metadata.csv の全体行は
    "objects/"）の出力。
    """
    base = root / "data" if bagged else root
    objects = base / "objects"
    objects.mkdir(parents=True)
    digests = _make_payload(objects)

    subdoc = base / "metadata" / "submissionDocumentation"

    rows = [FORMATS_HEADER]
    for rel, digest in digests.items():
        rows.append(f"{rel},Plain Text File,x-fmt/111,text/plain,,10,2026-01-01T00:00:00Z,{digest},検出なし")
    _write(subdoc / "formats.csv", "\r\n".join(rows) + "\r\n")

    # 旧 checksum.sha256（submissionDocumentation 配下・objects/ 付き）。
    _write(
        subdoc / "checksum.sha256",
        "\n".join(f"{d}  objects/{rel}" for rel, d in digests.items()) + "\n",
        bom=False,
    )

    # 旧 metadata.csv（全体行が "objects/"）。
    _write(
        base / "metadata" / "metadata.csv",
        "filename,dc.title\r\nobjects/,旧形式の全体記述\r\nobjects/a.txt,旧形式のファイル記述\r\n",
    )

    _write(
        subdoc / "description.csv",
        "Parent ID,Identifier,Title\r\n,2026-移管,総務課文書\r\n",
    )

    if bagged:
        (root / "bagit.txt").write_text(
            "BagIt-Version: 0.97\nTag-File-Character-Encoding: UTF-8\n", encoding="utf-8"
        )
        (root / "manifest-sha256.txt").write_text(
            "\n".join(f"{d}  data/objects/{rel}" for rel, d in digests.items()) + "\n",
            encoding="utf-8",
        )
    return root


def make_current_sip(out_parent: Path, source: Path, **opts) -> Path:
    """いまのパイプラインで SIP を作る。"""
    result = sip_pipeline.run(
        input_path=source,
        output_parent=out_parent,
        metadata=SIPMetadata(identifier="2026-移管", title="総務課文書"),
        options=SIPOptions(**opts),
        progress=lambda _m: None,
    )
    return result.sip_path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    src.mkdir()
    _make_payload(src)
    return src


class TestLegacyPackagesStillRead:
    def test_plain_legacy_sip_inherits_every_hash(self, tmp_path):
        """旧位置・旧表記の checksum.sha256 からハッシュを継承できること。

        ここが落ちると、過去の SIP は AIP 化のたびに全ファイルを再計算する。
        黙って再計算されるので、落ちたことに誰も気づけない。
        """
        parsed = sip_reader.read(make_legacy_sip(tmp_path / "legacy"))
        assert parsed.recomputed_hashes == 0
        assert parsed.inherited_hashes == len(PAYLOAD)

    def test_plain_legacy_sip_inherits_descriptions(self, tmp_path):
        """旧形式の metadata.csv（全体行が "objects/"）を読めること。"""
        parsed = sip_reader.read(make_legacy_sip(tmp_path / "legacy"))
        assert parsed.descriptive is not None
        assert parsed.descriptive.title == "旧形式の全体記述"
        by_path = {f.relative_path: f for f in parsed.files}
        assert by_path["a.txt"].descriptive.title == "旧形式のファイル記述"

    def test_plain_legacy_sip_inherits_formats(self, tmp_path):
        parsed = sip_reader.read(make_legacy_sip(tmp_path / "legacy"))
        assert {f.puid for f in parsed.files} == {"x-fmt/111"}

    def test_bagged_legacy_sip_reads(self, tmp_path):
        parsed = sip_reader.read(make_legacy_sip(tmp_path / "legacy-bag", bagged=True))
        assert sip_reader.detect_bag(tmp_path / "legacy-bag")
        assert parsed.recomputed_hashes == 0
        assert parsed.descriptive.title == "旧形式の全体記述"

    def test_hashes_survive_when_only_the_old_checksum_file_exists(self, tmp_path):
        """新しい metadata/checksum.sha256 が無くても困らないこと。

        旧 SIP にはそもそも存在しない。片方しか見ない実装に戻ると落ちる。
        """
        root = make_legacy_sip(tmp_path / "legacy")
        assert not (root / "metadata" / "checksum.sha256").exists()
        assert sip_reader.read(root).recomputed_hashes == 0


class TestCurrentPackagesRead:
    def test_plain_sip_inherits_every_hash(self, source, tmp_path):
        parsed = sip_reader.read(make_current_sip(tmp_path / "out", source))
        assert parsed.recomputed_hashes == 0
        assert parsed.inherited_hashes == len(PAYLOAD)

    def test_bagged_sip_inherits_every_hash(self, source, tmp_path):
        parsed = sip_reader.read(make_current_sip(tmp_path / "out-bag", source, make_bag=True))
        assert parsed.recomputed_hashes == 0

    def test_new_checksum_file_alone_is_enough(self, source, tmp_path):
        """新しい置き場だけを残しても継承できること。

        いまは 2 本置いている（読み手が 2 人いるため）が、その片方に
        寄りかかっていないことを確かめる。
        """
        root = make_current_sip(tmp_path / "out", source)
        (root / "metadata" / "submissionDocumentation" / "checksum.sha256").unlink()
        (root / "metadata" / "submissionDocumentation" / "formats.csv").unlink()
        parsed = sip_reader.read(root)
        assert parsed.recomputed_hashes == 0
        assert parsed.inherited_hashes == len(PAYLOAD)

    def test_whole_transfer_row_without_a_trailing_slash_is_understood(self, source, tmp_path):
        """新形式の全体行 "objects"（末尾スラッシュなし）を全体の記述として読むこと。"""
        root = make_current_sip(tmp_path / "out", source)
        (root / "metadata" / "metadata.csv").write_text(
            "﻿filename,dc.title\r\nobjects,新形式の全体記述\r\n", encoding="utf-8"
        )
        assert sip_reader.read(root).descriptive.title == "新形式の全体記述"

    def test_bagged_metadata_csv_paths_are_understood(self, source, tmp_path):
        """bag の "data/objects/<rel>" をファイル単位の記述として読むこと。

        bag のパスに data/ を足したのは Archivematica の仕様に合わせるため。
        足したまま自分が読めなくなっては、AIP 段で記述が落ちる。
        """
        root = make_current_sip(tmp_path / "out-bag", source, make_bag=True)
        (root / "data" / "metadata" / "metadata.csv").write_text(
            "﻿filename,dc.title\r\ndata/objects,全体\r\ndata/objects/a.txt,ファイル\r\n",
            encoding="utf-8",
        )
        parsed = sip_reader.read(root)
        assert parsed.descriptive.title == "全体"
        by_path = {f.relative_path: f for f in parsed.files}
        assert by_path["a.txt"].descriptive.title == "ファイル"
