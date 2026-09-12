"""来歴記録として正しいこと。

2026-09-11、実際に生成した SIP / AIP を人が読み、3 つの誤りが見つかった。
**いずれもパッケージの整合性ではなく、記録の中身の問題**だった。
ここに落として、同じ誤りが戻らないようにする。

1. 版番号が古いまま記録されていた（0.1.2 を動かして 0.1.0 と記録）
2. フォーマット識別とウイルス検査を実行したのに、記録していなかった
3. 提出書類が METS の fileSec に無かった（BagIt のマニフェストには有った）
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

import archival_packager
from archival_packager.core import dfxml, mets, spreadsheets
from archival_packager.core.aip_models import AIPFile, DescriptiveMetadata
from archival_packager.core.aip_pipeline import APP_AGENT_NAME
from archival_packager.core.models import ScannedFile

NS = {"mets": mets.METS_NS, "premis": mets.PREMIS_NS}


class TestVersionIsNotHardcoded:
    """版を直書きしないこと。

    **来歴記録が誤った道具名を主張していた。** 版を上げても、記録側の
    直書きが追随していなかった。来歴の正しさを担保する道具として
    軽くない誤りである。
    """

    def test_dfxml_reports_the_running_version(self):
        assert dfxml.PROGRAM_VERSION == archival_packager.__version__

    def test_premis_agent_reports_the_running_version(self):
        assert archival_packager.__version__ in APP_AGENT_NAME

    def test_no_literal_version_in_the_source(self):
        """版の文字列をコード中に直書きしないこと。

        **コメント行は見ない。** コメントは何も直書きできないので、
        ここで拾っても誤検知にしかならない。実際に
        「`gs --version` は "10.07.1" としか答えない」という説明の行を
        違反として報告し、説明のほうを削らせかけた（2026-09-12）。
        検査したいのは、動くコードが版を名乗ってしまうことである。
        """
        import re

        root = Path(archival_packager.__file__).parent
        offenders = []
        for path in root.rglob("*.py"):
            if path.name == "__init__.py":
                continue  # ここだけが版を持つ
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if re.search(r'"\d+\.\d+\.\d+"', line) and "version" in line.lower():
                    offenders.append(f"{path.name}:{i}")
        assert not offenders, (
            "版を直書きしている箇所: " + ", ".join(offenders)
            + "。archival_packager.__version__ を使うこと"
        )


def _file(rel: str, uuid: str, **kw) -> AIPFile:
    return AIPFile(
        relative_path=rel,
        absolute_path=Path("/x") / rel,
        size_bytes=10,
        uuid=uuid,
        sha256=kw.pop("sha256", "a" * 64),
        **kw,
    )


def _build(files, submission=None) -> etree._Element:
    return etree.fromstring(
        mets.build_mets(
            aip_uuid="aip-1",
            files=files,
            agents=[],
            descriptive=DescriptiveMetadata(title="移管"),
            created_iso="2026-09-11T00:00:00Z",
            submission_documentation=submission,
        )
    )


class TestProcessesAreRecorded:
    """実行した処理が、保存処理記録として残ること。

    論文にこう書いている — 「処理を実行すること自体が保存処理記録の作成になる」。
    識別も検査も実行していたのに、記録には ingestion と fixity check しか
    無かった。**PREMIS は、いつ・何を・どの道具で行ったかを残すためにある。**
    """

    def _events(self, doc: etree._Element) -> list[str]:
        return [
            e.text for e in doc.findall(".//premis:event/premis:eventType", NS)
        ]

    def test_format_identification_is_recorded(self):
        from archival_packager.core import aip_pipeline

        f = _file("objects/a.pdf", "u1", puid="fmt/19", format_name="Acrobat PDF 1.5")
        aip_pipeline._append_identification_event(f, "2026-09-11T00:00:00Z", ["agent"])
        doc = _build([f])
        assert "format identification" in self._events(doc)
        assert "fmt/19" in etree.tostring(doc, encoding="unicode")

    def test_unidentified_file_is_also_recorded(self):
        """識別できなかったことも記録に値する。

        あとから見た人が「識別しなかった」のか「識別できなかった」のかを
        区別できる。
        """
        from archival_packager.core import aip_pipeline

        f = _file("objects/x.bin", "u2")
        aip_pipeline._append_identification_event(f, "2026-09-11T00:00:00Z", ["agent"])
        assert f.events[-1].type == "format identification"
        assert f.events[-1].outcome == "fail"

    def test_virus_check_is_recorded_when_performed(self):
        from archival_packager.core import aip_pipeline

        f = _file("objects/a.pdf", "u3", virus_state="検出なし")
        aip_pipeline._append_virus_event(f, "2026-09-11T00:00:00Z", ["agent"])
        assert [e.type for e in f.events] == ["virus check"]
        assert f.events[0].outcome == "pass"

    def test_virus_check_is_not_recorded_when_skipped(self):
        """**実施していないときは書かない。**

        「検査して検出なし」と「検査していない」を取り違えられては困る。
        書かないことが「不明」を表す。
        """
        from archival_packager.core import aip_pipeline

        for state in ("未実施", "未実施（オプション OFF）", "", None):
            f = _file("objects/a.pdf", "u4", virus_state=state)
            aip_pipeline._append_virus_event(f, "2026-09-11T00:00:00Z", ["agent"])
            assert f.events == [], f"{state!r} で記録してはいけない"

    def test_detection_is_recorded_as_failure(self):
        from archival_packager.core import aip_pipeline

        f = _file("objects/a.pdf", "u5", virus_state="検出: Eicar-Test-Signature")
        aip_pipeline._append_virus_event(f, "2026-09-11T00:00:00Z", ["agent"])
        assert f.events[0].outcome == "fail"
        assert "Eicar" in f.events[0].detail_note


class TestVirusStateIsMachineReadable:
    """ウイルス検査の結果を、文章ではなく列で残すこと。

    レポートの文章の中だけだと、AIP を作るときに「検査したか」を辿れず、
    PREMIS に記録できない。
    """

    def _row(self, f: ScannedFile) -> list[str]:
        import csv
        import io

        text = spreadsheets.formats([f]).lstrip("﻿")
        return list(csv.reader(io.StringIO(text, newline="")))[1]

    def _scanned(self, **kw) -> ScannedFile:
        from datetime import UTC, datetime

        return ScannedFile(
            relative_path="a.txt",
            absolute_path=Path("/x/a.txt"),
            size_bytes=1,
            modified=datetime.fromtimestamp(0, UTC),
            **kw,
        )

    def test_column_exists(self):
        text = spreadsheets.formats([self._scanned()]).lstrip("﻿")
        assert "ウイルス検査" in text.splitlines()[0]

    def test_not_scanned_is_distinguishable_from_clean(self):
        """ここを取り違えると、確認していないものを確認済みとして扱うことになる。"""
        assert self._row(self._scanned())[-1] == spreadsheets.VIRUS_NOT_SCANNED
        assert (
            self._row(self._scanned(scanned_for_virus=True))[-1]
            == spreadsheets.VIRUS_CLEAN
        )

    def test_detection_names_the_signature(self):
        row = self._row(self._scanned(scanned_for_virus=True, virus="Eicar-Test"))
        assert "Eicar-Test" in row[-1]


class TestSubmissionDocumentationIsInTheMETS:
    """提出書類も METS に載せること。

    AIP に入れているのに fileSec に無いと、METS だけを読む側からは
    存在しないことになる。BagIt のマニフェストには入っていた。
    """

    def test_file_group_is_present(self):
        docs = [
            mets.SubmissionDocument(
                href="objects/submissionDocumentation/report.txt", uuid="d1"
            ),
            mets.SubmissionDocument(
                href="objects/submissionDocumentation/formats.csv", uuid="d2"
            ),
        ]
        doc = _build([_file("objects/a.pdf", "u1")], submission=docs)
        groups = {
            g.get("USE") for g in doc.findall(".//mets:fileSec/mets:fileGrp", NS)
        }
        assert "submissionDocumentation" in groups

    def test_every_document_is_listed(self):
        docs = [
            mets.SubmissionDocument(href=f"objects/submissionDocumentation/f{i}.csv",
                                    uuid=f"d{i}")
            for i in range(7)
        ]
        doc = _build([_file("objects/a.pdf", "u1")], submission=docs)
        hrefs = [
            f.get(f"{{{mets.XLINK_NS}}}href")
            for f in doc.findall(
                ".//mets:fileGrp[@USE='submissionDocumentation']/mets:file/mets:FLocat",
                NS,
            )
        ]
        assert len(hrefs) == 7

    def test_documents_have_no_admid(self):
        """提出書類は PREMIS の object を持たない。存在しない ID を指さないこと。"""
        docs = [mets.SubmissionDocument(href="objects/submissionDocumentation/r.txt",
                                        uuid="d1")]
        doc = _build([_file("objects/a.pdf", "u1")], submission=docs)
        node = doc.find(
            ".//mets:fileGrp[@USE='submissionDocumentation']/mets:file", NS
        )
        assert node.get("ADMID") is None

    def test_absent_when_there_is_nothing(self):
        doc = _build([_file("objects/a.pdf", "u1")], submission=[])
        groups = {g.get("USE") for g in doc.findall(".//mets:fileSec/mets:fileGrp", NS)}
        assert "submissionDocumentation" not in groups


class TestSourcePathDoesNotLeakTheOperator:
    """入力元の記録に、利用者名を埋め込まないこと。

    絶対パスには C:\\Users\\<名前> が入る。AIP は外部に渡りうるもので、
    **このアプリ自身が個人情報を検出する機能を持っている。**
    自分が利用者名を埋め込むのは筋が通らない。
    """

    def _source(self, root: Path, **kw) -> str:
        xml = dfxml.build([], root, **kw)
        return etree.fromstring(xml).findtext("source/image_filename") or ""

    def test_only_the_folder_name_by_default(self):
        got = self._source(Path("/Users/nakamura/Desktop/移管 2026"))
        assert got == "移管 2026"
        assert "nakamura" not in got

    def test_windows_home_is_not_leaked(self):
        import ntpath

        # Windows 形式のパスでも、利用者名が残らないこと。
        got = self._source(Path(ntpath.basename(r"C:\Users\nakam\Desktop\移管")))
        assert "nakam" not in got

    def test_full_path_is_available_when_asked(self):
        """組織の方針として完全なパスを残したい場合は選べること。"""
        root = Path("/Users/nakamura/Desktop/移管 2026")
        assert self._source(root, full_source_path=True) == str(root)

