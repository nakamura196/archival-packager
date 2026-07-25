"""METS / PREMIS 生成のテスト。

現行 Swift 実装は XML を文字列連結で組んでいたため、エスケープ漏れや構造の誤りを
検出できなかった。ここでは生成結果を実際にパースして構造を検査する
（文字列一致で見ると、壊れた XML でもテストが通ってしまう）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import mets
from archival_packager.core.aip_models import (
    AgentKind,
    AIPFile,
    Derivative,
    DerivativePurpose,
    DescriptiveMetadata,
    PremisAgent,
    PremisEvent,
)
from archival_packager.core.mets import DC_NS, METS_NS, PREMIS_NS, XLINK_NS, XSI_NS

NS = {"mets": METS_NS, "premis": PREMIS_NS, "xlink": XLINK_NS, "xsi": XSI_NS, "dc": DC_NS}


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


def build(files, agents=None, descriptive=None) -> etree._Element:
    xml = mets.build_mets(
        aip_uuid="aip-uuid",
        files=files,
        agents=agents or [],
        descriptive=descriptive,
        created_iso="2026-07-25T00:00:00Z",
    )
    return etree.fromstring(xml)


class TestWellFormedness:
    def test_output_parses(self):
        assert build([a_file("a.pdf", "u1")]) is not None

    def test_declares_utf8(self):
        xml = mets.build_mets(
            aip_uuid="x", files=[a_file("a.pdf", "u1")], agents=[],
            descriptive=None, created_iso="2026-07-25T00:00:00Z",
        )
        assert xml.startswith(b"<?xml version=")
        assert b"UTF-8" in xml.split(b"\n", 1)[0]

    @pytest.mark.parametrize(
        "hostile",
        [
            "R&D レポート.pdf",
            "a<b>c.pdf",
            'quote"inside.pdf',
            "apos'inside.pdf",
            "セミコロン;と&アンパサンド.pdf",
        ],
    )
    def test_hostile_filenames_do_not_break_the_document(self, hostile):
        """XML 特殊文字を含む資料名。

        文字列連結の実装ではエスケープ漏れが 1 箇所でもあるとここで壊れる。
        壊れても生成自体は成功してしまうため、パースして確かめる必要がある。
        """
        root = build([a_file(hostile, "u1")])
        names = root.xpath("//premis:originalName/text()", namespaces=NS)
        assert names == [hostile], "値が欠けたり化けたりしていない"

    def test_hostile_descriptive_values(self):
        d = DescriptiveMetadata(title="A & B <C>", description='He said "hi"')
        root = build([a_file("a.pdf", "u1")], descriptive=d)
        assert root.xpath("//dc:title/text()", namespaces=NS) == ["A & B <C>"]


class TestStructure:
    def test_sections_present_in_order(self):
        root = build([a_file("a.pdf", "u1")], agents=[PremisAgent("app", "App", AgentKind.SOFTWARE)])
        tags = [etree.QName(e).localname for e in root]
        assert tags[0] == "metsHdr"
        assert "amdSec" in tags and "fileSec" in tags and "structMap" in tags
        assert tags.index("fileSec") < tags.index("structMap")

    def test_admid_links_file_to_amdsec(self):
        """fileSec の ADMID が実在する amdSec を指していること。

        3 セクション間で ID が一貫していないと METS として意味を成さない。
        """
        root = build([a_file("a.pdf", "u1")])
        adm_ids = set(root.xpath("//mets:amdSec/@ID", namespaces=NS))
        for admid in root.xpath("//mets:file/@ADMID", namespaces=NS):
            assert admid in adm_ids

    def test_fptr_links_to_existing_file_id(self):
        root = build([a_file("a.pdf", "u1"), a_file("d/b.pdf", "u2")])
        file_ids = set(root.xpath("//mets:file/@ID", namespaces=NS))
        fptrs = root.xpath("//mets:fptr/@FILEID", namespaces=NS)
        assert fptrs, "structMap から fileSec への参照がある"
        for fid in fptrs:
            assert fid in file_ids

    def test_flocat_uses_xlink_href(self):
        root = build([a_file("a.pdf", "u1")])
        hrefs = root.xpath("//mets:FLocat/@xlink:href", namespaces=NS)
        assert hrefs == ["objects/a.pdf"]

    def test_nested_directories_become_nested_divs(self):
        root = build([a_file("x/y/deep.pdf", "u1")])
        labels = root.xpath("//mets:structMap//mets:div/@LABEL", namespaces=NS)
        assert labels == ["objects", "x", "y", "deep.pdf"]

    def test_ordering_is_deterministic(self):
        """走査順に依存すると、同じ資料から作った AIP を突合できなくなる。"""
        forward = build([a_file(n, f"u{i}") for i, n in enumerate(["b.pdf", "a.pdf", "c.pdf"])])
        reverse = build([a_file(n, f"u{i}") for i, n in enumerate(["c.pdf", "a.pdf", "b.pdf"])])
        labels_f = forward.xpath("//mets:structMap//mets:div[@TYPE='item']/@LABEL", namespaces=NS)
        labels_r = reverse.xpath("//mets:structMap//mets:div[@TYPE='item']/@LABEL", namespaces=NS)
        assert labels_f == labels_r == ["a.pdf", "b.pdf", "c.pdf"]


class TestPremisObject:
    def test_fixity_recorded_as_sha256(self):
        root = build([a_file("a.pdf", "u1", sha256="b" * 64)])
        assert root.xpath("//premis:messageDigestAlgorithm/text()", namespaces=NS) == ["SHA-256"]
        assert root.xpath("//premis:messageDigest/text()", namespaces=NS) == ["b" * 64]

    def test_fixity_omitted_when_unknown(self):
        """ハッシュが無いのに空要素を出すと「照合済みで空」と誤読される。"""
        root = build([a_file("a.pdf", "u1", sha256=None)])
        assert root.xpath("//premis:fixity", namespaces=NS) == []

    def test_pronom_registry_recorded(self):
        root = build([a_file("a.pdf", "u1", puid="fmt/19")])
        assert root.xpath("//premis:formatRegistryName/text()", namespaces=NS) == ["PRONOM"]
        assert root.xpath("//premis:formatRegistryKey/text()", namespaces=NS) == ["fmt/19"]

    def test_unknown_format_falls_back(self):
        root = build([a_file("a.bin", "u1", puid=None, format_name=None)])
        assert root.xpath("//premis:formatName/text()", namespaces=NS) == ["unknown"]
        assert root.xpath("//premis:formatRegistry", namespaces=NS) == []

    def test_object_is_typed_as_file(self):
        root = build([a_file("a.pdf", "u1")])
        assert root.xpath("//premis:object/@xsi:type", namespaces=NS) == ["premis:file"]


class TestDerivatives:
    def _with_derivative(self):
        f = a_file("a.eps", "u1", puid="fmt/122")
        f.derivatives = [
            Derivative(
                purpose=DerivativePurpose.PRESERVATION,
                path=Path("/x/a.pdf"),
                relative_path="a.pdf",
                size_bytes=99,
                uuid="d1",
                tool_name="gs",
                command_line="gs ...",
                sha256="c" * 64,
                puid_out="fmt/276",
            )
        ]
        return build([f])

    def test_preservation_filegrp_created(self):
        root = self._with_derivative()
        uses = root.xpath("//mets:fileGrp/@USE", namespaces=NS)
        assert uses == ["original", "preservation"]

    def test_derivation_relationship_points_at_the_original(self):
        root = self._with_derivative()
        assert root.xpath("//premis:relationshipType/text()", namespaces=NS) == ["derivation"]
        assert root.xpath("//premis:relationshipSubType/text()", namespaces=NS) == [
            "is normalized version of"
        ]
        assert root.xpath("//premis:relatedObjectIdentifierValue/text()", namespaces=NS) == ["u1"]

    def test_no_preservation_group_without_derivatives(self):
        root = build([a_file("a.pdf", "u1")])
        assert root.xpath("//mets:fileGrp/@USE", namespaces=NS) == ["original"]


class TestEventsAndAgents:
    def test_event_identifier_is_stable(self):
        """Swift 版は生成のたびに UUID を振っていたため、同じ入力でも出力が変わった。

        差分検証ができるよう、識別子はモデルが持つ。
        """
        ev = PremisEvent(
            type="fixity check", date_time="2026-07-25T00:00:00Z",
            detail_note="", outcome="pass", identifier="fixed-id",
        )
        f = a_file("a.pdf", "u1")
        f.events = [ev]
        first = build([f]).xpath("//premis:eventIdentifierValue/text()", namespaces=NS)
        second = build([f]).xpath("//premis:eventIdentifierValue/text()", namespaces=NS)
        assert first == second == ["fixed-id"]

    def test_linking_agent_type_from_kind(self):
        agents = [
            PremisAgent("app", "Archival Packager", AgentKind.SOFTWARE),
            PremisAgent("nakamura", "中村", AgentKind.HUMAN),
        ]
        f = a_file("a.pdf", "u1")
        f.events = [
            PremisEvent("ingestion", "2026-07-25T00:00:00Z", "", "success",
                        agent_ids=["app", "nakamura"], identifier="e1")
        ]
        root = build([f], agents=agents)
        types = root.xpath("//premis:linkingAgentIdentifierType/text()", namespaces=NS)
        assert types == ["preservation-system", "archivist"]

    def test_unknown_agent_falls_back_to_preservation_system(self):
        f = a_file("a.pdf", "u1")
        f.events = [
            PremisEvent("x", "t", "", "ok", agent_ids=["not-registered"], identifier="e1")
        ]
        root = build([f], agents=[])
        assert root.xpath("//premis:linkingAgentIdentifierType/text()", namespaces=NS) == [
            "preservation-system"
        ]

    def test_agents_collected_in_one_amdsec(self):
        agents = [
            PremisAgent("app", "App", AgentKind.SOFTWARE),
            PremisAgent("org", "東京大学", AgentKind.ORGANIZATION),
        ]
        root = build([a_file("a.pdf", "u1")], agents=agents)
        names = root.xpath("//mets:amdSec[@ID='amdSec_agents']//premis:agentName/text()", namespaces=NS)
        assert names == ["App", "東京大学"]

    def test_event_detail_omitted_when_empty(self):
        f = a_file("a.pdf", "u1")
        f.events = [PremisEvent("x", "t", "", "ok", identifier="e1")]
        root = build([f])
        assert root.xpath("//premis:eventOutcomeDetail", namespaces=NS) == []


class TestDescriptiveMetadata:
    def test_whole_dmd_linked_from_top_div(self):
        d = DescriptiveMetadata(title="移管一式")
        root = build([a_file("a.pdf", "u1")], descriptive=d)
        top = root.xpath("//mets:structMap/mets:div", namespaces=NS)[0]
        assert top.get("DMDID") == "dmdSec_whole"

    def test_no_dmdsec_when_all_fields_empty(self):
        root = build([a_file("a.pdf", "u1")], descriptive=DescriptiveMetadata())
        assert root.xpath("//mets:dmdSec", namespaces=NS) == []
        assert root.xpath("//mets:structMap/mets:div/@DMDID", namespaces=NS) == []

    def test_file_level_dmd_linked_from_item_div(self):
        f = a_file("a.pdf", "u1", descriptive=DescriptiveMetadata(title="個別タイトル"))
        root = build([f])
        item = root.xpath("//mets:div[@TYPE='item']", namespaces=NS)[0]
        assert item.get("DMDID") == "dmdSec_obj0"

    def test_empty_fields_are_omitted(self):
        d = DescriptiveMetadata(title="only title")
        root = build([a_file("a.pdf", "u1")], descriptive=d)
        dc_children = root.xpath("//mets:dmdSec//dcterms:dublincore/*",
                                 namespaces={**NS, "dcterms": "http://purl.org/dc/terms/"})
        assert len(dc_children) == 1


class TestDescriptiveMerge:
    def test_user_wins_over_inherited(self):
        merged = DescriptiveMetadata.merge(
            DescriptiveMetadata(title="ユーザ入力"),
            DescriptiveMetadata(title="継承", creator="継承作成者"),
        )
        assert merged is not None
        assert merged.title == "ユーザ入力"
        assert merged.creator == "継承作成者", "空欄は継承値で埋まる"

    def test_returns_none_when_nothing_to_show(self):
        assert DescriptiveMetadata.merge(None, None) is None
        assert DescriptiveMetadata.merge(DescriptiveMetadata(), DescriptiveMetadata()) is None
