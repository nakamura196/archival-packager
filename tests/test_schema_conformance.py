"""生成した METS / PREMIS が、公式のスキーマに適合すること。

**「自分が想定した構造になっている」と「仕様に適合している」は別である。**
他のテストは前者を固定している。要素名の綴り、属性の必須／任意、出現順の
制約は、スキーマでしか捕まらない。

論文で「PREMIS に準拠した保存処理記録を含む METS」と述べる以上、
機械で確かめられる形にしておく。

スキーマは tests/schemas/ に置いてある（METS 1.12.1 と PREMIS 3.0、
いずれも米国議会図書館の配布物）。**取りに行かない。** 外部に依存すると、
先方が落ちた日に CI が止まる。参照先は手元のファイルに向け直してある。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import mets
from archival_packager.core.aip_models import (
    AIPFile,
    Derivative,
    DerivativePurpose,
    DescriptiveMetadata,
    PremisEvent,
)

SCHEMAS = Path(__file__).parent / "schemas"


@pytest.fixture(scope="module")
def mets_schema() -> etree.XMLSchema:
    """METS と PREMIS を一緒に読み込んだもの。

    生成物は amdSec に PREMIS を埋め、xsi:type で PREMIS 側の型を指す。
    METS のスキーマだけでは、その型を解決できない。
    """
    return etree.XMLSchema(etree.parse(str(SCHEMAS / "mets-with-premis.xsd")))


@pytest.fixture(scope="module")
def premis_schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(SCHEMAS / "premis.xsd")))


def _explain(schema: etree.XMLSchema) -> str:
    return "\n".join(f"  {e.line}: {e.message}" for e in schema.error_log)


def a_file(rel: str, uuid: str, **kw) -> AIPFile:
    return AIPFile(
        relative_path=rel,
        absolute_path=Path("/x") / rel,
        size_bytes=kw.pop("size_bytes", 10),
        uuid=uuid,
        sha256=kw.pop("sha256", "a" * 64),
        puid=kw.pop("puid", "fmt/19"),
        format_name=kw.pop("format_name", "Acrobat PDF 1.5"),
        **kw,
    )


def build(files, agents=None, descriptive=None, submission=None) -> etree._Element:
    xml = mets.build_mets(
        aip_uuid="aip-uuid",
        files=files,
        agents=agents or [],
        descriptive=descriptive,
        created_iso="2026-09-11T00:00:00Z",
        submission_documentation=submission,
    )
    return etree.fromstring(xml)


def an_event(kind: str = "ingestion", outcome: str = "success") -> PremisEvent:
    return PremisEvent(
        type=kind,
        date_time="2026-09-11T00:00:00Z",
        detail_note="AIP 化のため取り込み",
        outcome=outcome,
        agent_ids=["Archival Packager"],
    )


class TestGeneratedMETSValidates:
    def test_minimal_document_is_valid(self, mets_schema):
        doc = build([a_file("objects/a.pdf", "u1")],
                    descriptive=DescriptiveMetadata(title="移管 2026"))
        assert mets_schema.validate(doc), (
            "生成した METS がスキーマに適合しない:\n" + _explain(mets_schema)
        )

    def test_hostile_names_still_validate(self, mets_schema):
        """資料名に & や < が入っても、壊れた XML を出さないこと。"""
        doc = build(
            [a_file('objects/a&b<c>"d".txt', "u2")],
            descriptive=DescriptiveMetadata(title="記号 & < > の入った移管"),
        )
        assert mets_schema.validate(doc), (
            "記号を含む名前で METS が適合しない:\n" + _explain(mets_schema)
        )

    def test_document_with_events_validates(self, mets_schema):
        f = a_file("objects/a.pdf", "u3")
        f.events.append(an_event())
        f.events.append(an_event("fixity check", "pass"))
        doc = build([f], descriptive=DescriptiveMetadata(title="処理記録あり"))
        assert mets_schema.validate(doc), (
            "保存処理記録を含む METS が適合しない:\n" + _explain(mets_schema)
        )

    def test_document_with_derivative_validates(self, mets_schema):
        """派生物（保存用に変換したもの）を含む場合も適合すること。"""
        f = a_file("objects/a.png", "u4", puid="fmt/11")
        f.derivatives.append(
            Derivative(
                purpose=DerivativePurpose.PRESERVATION,
                path=Path("/x/objects/a.tiff"),
                relative_path="objects/a.tiff",
                size_bytes=20,
                uuid="u4-d1",
                sha256="b" * 64,
                tool_name="Pillow",
                command_line="pillow a.png a.tiff",
            )
        )
        doc = build([f], descriptive=DescriptiveMetadata(title="派生物あり"))
        assert mets_schema.validate(doc), (
            "派生物を含む METS が適合しない:\n" + _explain(mets_schema)
        )


    def test_submission_documentation_group_validates(self, mets_schema):
        """提出書類の fileGrp を足しても適合すること。

        ADMID を付けない file を含むため、スキーマ上許されるかを確かめる。
        """
        doc = build(
            [a_file("objects/a.pdf", "u6")],
            descriptive=DescriptiveMetadata(title="提出書類あり"),
            submission=[
                mets.SubmissionDocument(
                    href="objects/submissionDocumentation/report.txt", uuid="d1"
                )
            ],
        )
        assert mets_schema.validate(doc), (
            "提出書類を含む METS が適合しない:\n" + _explain(mets_schema)
        )

    def test_identification_and_virus_events_validate(self, mets_schema):
        """識別・検査のイベントを足しても適合すること。"""
        from archival_packager.core import aip_pipeline

        f = a_file("objects/a.pdf", "u7", virus_state="検出なし")
        aip_pipeline._append_identification_event(f, "2026-09-11T00:00:00Z", ["a"])
        aip_pipeline._append_virus_event(f, "2026-09-11T00:00:00Z", ["a"])
        doc = build([f], descriptive=DescriptiveMetadata(title="識別と検査"))
        assert mets_schema.validate(doc), (
            "識別・検査のイベントを含む METS が適合しない:\n" + _explain(mets_schema)
        )


class TestEmbeddedPREMISValidates:
    """METS の中に埋めた PREMIS を、PREMIS 単体としても検証する。

    METS のスキーマは amdSec の中身を任意の XML として許すため、
    METS が通っても PREMIS が正しいとは限らない。別に確かめる。
    """

    def test_premis_nodes_validate_on_their_own(self, premis_schema):
        f = a_file("objects/a.pdf", "u5")
        f.events.append(an_event("fixity check", "pass"))
        doc = build([f], descriptive=DescriptiveMetadata(title="検証用"))
        ns = {"premis": mets.PREMIS_NS}
        nodes = doc.findall(".//premis:object", ns) + doc.findall(".//premis:event", ns)
        assert nodes, "PREMIS の object / event が入っていない"
        for node in nodes:
            standalone = etree.fromstring(etree.tostring(node))
            assert premis_schema.validate(standalone), (
                f"PREMIS の {etree.QName(node).localname} が適合しない:\n"
                + _explain(premis_schema)
            )


class TestSchemasAreVendored:
    """スキーマを手元に持ち、取りに行かないこと。"""

    def test_no_remote_references_remain(self):
        import re

        for path in SCHEMAS.glob("*.xsd"):
            remote = re.findall(
                r'schemaLocation="(https?://[^"]+)"', path.read_text(encoding="utf-8")
            )
            assert not remote, (
                f"{path.name} が外部を参照している: {remote}。"
                "先方が落ちた日に CI が止まる"
            )
