"""SIP を作るときの処理が、PREMIS の記録として残り、AIP に引き継がれること。

2026-10-27 まで、SIP を作るときのウイルス検査・識別・チェックサムの算出は
formats.csv の列にしか残らず、AIP 側が「AIP を作った時刻」で記録を作り直して
いた。SIP から AIP まで日が空くと、記録の日時が事実と違う。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from archival_packager.core import (
    aip_pipeline,
    bundled,
    clamav,
    mets,
    package_report,
    siegfried,
    sip_mets,
    sip_pipeline,
    sip_premis,
)
from archival_packager.core.aip_models import AIPOptions, AIPPipelineError
from archival_packager.core.models import SIPMetadata, SIPOptions

SCHEMAS = Path(__file__).parent / "schemas"
PREMIS_NS = sip_premis.PREMIS_NS
NS = {"premis": PREMIS_NS}

#: SIP を作った時刻として固定する値。AIP を作る時刻と必ず違う。
SIP_TIME = "2026-01-02T03:04:05Z"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    src = tmp_path / "in"
    (src / "文書").mkdir(parents=True)
    (src / "a.txt").write_text("資料 A\n", encoding="utf-8")
    (src / "文書" / "b&c.txt").write_text("資料 B\n", encoding="utf-8")
    return src


def make_sip(source: Path, tmp_path: Path, monkeypatch, **opts) -> Path:
    monkeypatch.setattr(sip_premis, "now_iso", lambda: SIP_TIME)
    out = tmp_path / "sip-out"
    out.mkdir(exist_ok=True)
    return sip_pipeline.run(
        input_path=source,
        output_parent=out,
        metadata=SIPMetadata(identifier="2026-移管", title="総務課文書"),
        options=SIPOptions(**opts),
    ).sip_path


def make_aip(sip: Path, tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.undo()
    out = tmp_path / "aip-out"
    out.mkdir(exist_ok=True)
    return aip_pipeline.run(sip_root=sip, output_parent=out, options=AIPOptions()).aip_path


def premis_of(sip: Path) -> etree._Element:
    return etree.parse(str(sip / "metadata" / "submissionDocumentation" / "premis.xml")).getroot()


def event_types(doc: etree._Element) -> list[str]:
    return [e.text for e in doc.iterfind(".//premis:event/premis:eventType", NS)]


@pytest.fixture
def fake_clamav(monkeypatch, tmp_path):
    """ClamAV が使える状態を作る。b&c.txt だけを検出したことにする。"""
    monkeypatch.setattr(clamav, "find_tool", lambda: Path("/fake/clamscan"))
    monkeypatch.setattr(clamav, "has_database", lambda _d=None: True)

    def scan(root: Path, **_kw):
        return {str(p): "Eicar-Test-Signature" for p in root.rglob("b*.txt")}

    monkeypatch.setattr(clamav, "scan", scan)


needs_siegfried = pytest.mark.skipif(
    bundled.find("sf") is None, reason="siegfried が同梱されていない"
)


class TestSIPWritesPremis:
    def test_valid_premis(self, source, tmp_path, monkeypatch):
        """書いた記録がスキーマに合わなければ、ほかのシステムでは読めない。"""
        doc = premis_of(make_sip(source, tmp_path, monkeypatch))
        schema = etree.XMLSchema(etree.parse(str(SCHEMAS / "premis.xsd")))
        assert schema.validate(doc), schema.error_log

    def test_records_the_time_of_the_sip(self, source, tmp_path, monkeypatch):
        doc = premis_of(make_sip(source, tmp_path, monkeypatch))
        dates = {e.text for e in doc.iterfind(".//premis:eventDateTime", NS)}
        assert dates == {SIP_TIME}

    def test_checksum_calculation_is_recorded(self, source, tmp_path, monkeypatch):
        doc = premis_of(make_sip(source, tmp_path, monkeypatch))
        assert event_types(doc).count("message digest calculation") == 2

    @needs_siegfried
    def test_identification_is_recorded(self, source, tmp_path, monkeypatch):
        doc = premis_of(make_sip(source, tmp_path, monkeypatch))
        assert event_types(doc).count("format identification") == 2

    def test_identification_not_recorded_without_the_tool(self, source, tmp_path, monkeypatch):
        """道具が無くて識別しなかったのに「識別できなかった (fail)」と書かないこと。"""
        monkeypatch.setattr(bundled, "find", lambda _name: None)
        doc = premis_of(make_sip(source, tmp_path, monkeypatch))
        assert "format identification" not in event_types(doc)

    def test_no_virus_event_when_not_scanned(self, source, tmp_path, monkeypatch):
        """検査していないのに「検出なし」と読める記録を書かないこと。"""
        doc = premis_of(make_sip(source, tmp_path, monkeypatch, scan_virus=False))
        assert "virus check" not in event_types(doc)

    def test_no_virus_event_without_clamav(self, source, tmp_path, monkeypatch):
        monkeypatch.setattr(clamav, "find_tool", lambda: None)
        doc = premis_of(make_sip(source, tmp_path, monkeypatch, scan_virus=True))
        assert "virus check" not in event_types(doc)

    def test_virus_scan_is_recorded_per_file(self, source, tmp_path, monkeypatch, fake_clamav):
        doc = premis_of(make_sip(source, tmp_path, monkeypatch, scan_virus=True))
        outcomes = {}
        for ev in doc.iterfind("premis:event", NS):
            if ev.findtext("premis:eventType", namespaces=NS) != "virus check":
                continue
            target = ev.findtext(
                "premis:linkingObjectIdentifier/premis:linkingObjectIdentifierValue",
                namespaces=NS,
            )
            outcomes[target] = ev.findtext(".//premis:eventOutcome", namespaces=NS)
            agents = [a.text for a in ev.iterfind(
                "premis:linkingAgentIdentifier/premis:linkingAgentIdentifierValue", NS)]
            assert "ClamAV" in agents
        assert outcomes == {"a.txt": "pass", "文書/b&c.txt": "fail"}
        names = [a.text for a in doc.iterfind(".//premis:agent/premis:agentName", NS)]
        assert "ClamAV" in names

    def test_filename_change_only_for_renamed_files(self, source, tmp_path, monkeypatch):
        doc = premis_of(make_sip(source, tmp_path, monkeypatch, sanitize_filenames=True))
        changes = [
            ev for ev in doc.iterfind("premis:event", NS)
            if ev.findtext("premis:eventType", namespaces=NS) == "filename change"
        ]
        assert len(changes) == 1
        note = changes[0].findtext(".//premis:eventOutcomeDetailNote", namespaces=NS)
        assert note.startswith("文書/b&c.txt → ")

    def test_bag_also_has_premis(self, source, tmp_path, monkeypatch):
        sip = make_sip(source, tmp_path, monkeypatch, make_bag=True)
        assert (sip / "data" / "metadata" / "submissionDocumentation" / "premis.xml").is_file()


class TestToolVersions:
    """agent に道具の版を書くこと。

    2026-10-28 まで、agent は "ClamAV" / "Siegfried (PRONOM)" と名前だけだった。
    識別の結果はシグネチャの版で変わり、「検出なし」の重みは定義 DB の新しさで
    決まる。版が無いと、あとから記録を見た人がその結果をどこまで信じてよいか
    判断できない。JHOVE は最初から版を書いているので、それに揃える。
    """

    SF_OUTPUT = (
        "siegfried 1.11.6\n"
        "/x/default.sig (2026-07-12T19:05:09+10:00)\n"
        "identifiers: \n"
        "  - pronom: DROID_SignatureFile_V124.xml; container-signature-20260119.xml\n"
    )

    def test_parse_siegfried_release(self):
        assert siegfried.parse_release(self.SF_OUTPUT) == (
            "Siegfried 1.11.6 "
            "(DROID_SignatureFile_V124.xml; container-signature-20260119.xml)"
        )

    def test_parse_siegfried_release_rejects_other_output(self):
        assert siegfried.parse_release("") is None
        assert siegfried.parse_release("[FATAL] error opening signature file") is None

    def test_parse_clamav_release(self):
        assert clamav.parse_release("ClamAV 1.5.3/28121/Fri Sep 11 23:24:41 2026\n") == (
            "ClamAV 1.5.3 (signatures 28121, Fri Sep 11 23:24:41 2026)"
        )

    def test_parse_clamav_release_without_database(self):
        """定義 DB を読めなかったときは版だけ。定義の版をでっち上げない。"""
        assert clamav.parse_release("ClamAV 1.5.3\n") == "ClamAV 1.5.3"
        assert clamav.parse_release("") is None

    def test_release_is_none_when_the_tool_cannot_run(self, tmp_path):
        missing = tmp_path / "no-such-tool"
        assert clamav.release(tool=missing, database=tmp_path) is None
        assert siegfried.release(tool=missing, home=tmp_path) is None

    def test_virus_agent_carries_the_version(self, source, tmp_path, monkeypatch, fake_clamav):
        """SIP の premis.xml にも、AIP の METS にも、版つきの agent が載ること。"""
        agent = "ClamAV 1.5.3 (signatures 28121, Fri Sep 11 23:24:41 2026)"
        monkeypatch.setattr(clamav, "release", lambda **_kw: agent)
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        doc = premis_of(sip)
        for ev in doc.iterfind("premis:event", NS):
            if ev.findtext("premis:eventType", namespaces=NS) == "virus check":
                agents = [a.text for a in ev.iterfind(
                    "premis:linkingAgentIdentifier/premis:linkingAgentIdentifierValue", NS)]
                assert agent in agents
        assert agent in [a.text for a in doc.iterfind(".//premis:agent/premis:agentName", NS)]

        aip = make_aip(sip, tmp_path, monkeypatch)
        mets_doc = etree.parse(str(package_report.find_mets(aip))).getroot()
        names = {a.text for a in mets_doc.iterfind(".//premis:agent/premis:agentName", NS)}
        assert agent in names

    @needs_siegfried
    def test_bundled_siegfried_reports_its_version(self, source, tmp_path, monkeypatch):
        doc = premis_of(make_sip(source, tmp_path, monkeypatch))
        names = [a.text for a in doc.iterfind(".//premis:agent/premis:agentName", NS)]
        assert any(n.startswith("Siegfried ") and "DROID_SignatureFile" in n for n in names), names


class TestAIPInheritsTheRecord:
    def test_sip_events_keep_their_time_and_identifier(
        self, source, tmp_path, monkeypatch, fake_clamav
    ):
        """**ここが本題。** AIP を作った時刻に書き換えないこと。"""
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        sip_ids = {e.text for e in premis_of(sip).iterfind(".//premis:eventIdentifierValue", NS)}
        aip = make_aip(sip, tmp_path, monkeypatch)

        report = package_report.read(aip)
        virus = [e for e in report.events if e.event_type == "virus check"]
        assert len(virus) == 2
        assert {e.date_time for e in virus} == {SIP_TIME}
        ingestion = [e for e in report.events if e.event_type == "ingestion"]
        assert ingestion and all(e.date_time != SIP_TIME for e in ingestion)

        mets_doc = etree.parse(str(package_report.find_mets(aip))).getroot()
        aip_ids = {e.text for e in mets_doc.iterfind(".//premis:eventIdentifierValue", NS)}
        assert sip_ids <= aip_ids

    def test_events_are_not_duplicated(self, source, tmp_path, monkeypatch, fake_clamav):
        """premis.xml から引き継いだうえで、formats.csv からも作り直さないこと。"""
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        aip = make_aip(sip, tmp_path, monkeypatch)
        report = package_report.read(aip)
        per_file: dict[tuple[str, str], int] = {}
        for e in report.events:
            per_file[(e.target, e.event_type)] = per_file.get((e.target, e.event_type), 0) + 1
        assert all(n == 1 for n in per_file.values()), per_file

    def test_tools_are_agents_in_the_mets(self, source, tmp_path, monkeypatch, fake_clamav):
        """event が指す agent が METS のどこにも無い、という状態にしない。"""
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        aip = make_aip(sip, tmp_path, monkeypatch)
        mets_doc = etree.parse(str(package_report.find_mets(aip))).getroot()
        names = {a.text for a in mets_doc.iterfind(".//premis:agent/premis:agentName", NS)}
        assert "ClamAV" in names

    def test_old_sip_still_gets_a_record(self, source, tmp_path, monkeypatch, fake_clamav):
        """premis.xml の無い古い SIP では、これまでどおり formats.csv から記録を作る。"""
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        (sip / "metadata" / "submissionDocumentation" / "premis.xml").unlink()
        aip = make_aip(sip, tmp_path, monkeypatch)
        types = {e.event_type for e in package_report.read(aip).events}
        assert {"virus check", "format identification"} <= types

    def test_broken_premis_stops_the_aip(self, source, tmp_path, monkeypatch):
        """壊れた記録を黙って無視すると、日時がすり替わった記録で AIP ができる。"""
        sip = make_sip(source, tmp_path, monkeypatch)
        (sip / "metadata" / "submissionDocumentation" / "premis.xml").write_text("<broken")
        with pytest.raises(AIPPipelineError):
            make_aip(sip, tmp_path, monkeypatch)


class TestSIPWritesMets:
    """SIP の記録を METS にも包んで書き、AIP では Archivematica と同じ位置に置く（#49）。

    Archivematica の AIP は受け入れ時の METS を
    submissionDocumentation/transfer-<名前>-<uuid>/METS.xml に持つ。
    こちらは premis.xml しか無く、同じ形を前提に読む道具からは記録が見つからなかった。
    """

    def mets_of(self, sip: Path, *parts: str) -> etree._Element:
        base = sip.joinpath(*parts) if parts else sip
        path = base / "metadata" / "submissionDocumentation" / sip_mets.FILE_NAME
        return etree.parse(str(path)).getroot()

    def test_valid_mets(self, source, tmp_path, monkeypatch, fake_clamav):
        """METS と PREMIS のスキーマに合うこと。合わなければ他のシステムでは読めない。"""
        doc = self.mets_of(make_sip(source, tmp_path, monkeypatch, scan_virus=True))
        schema = etree.XMLSchema(etree.parse(str(SCHEMAS / "mets-with-premis.xsd")))
        assert schema.validate(doc), schema.error_log

    def test_same_events_as_premis(self, source, tmp_path, monkeypatch, fake_clamav):
        """premis.xml と同じ記録であること。event の識別子まで一致する。

        2 つの形で書くので、片方だけ違う中身になると、どちらが正しいか分からなくなる。
        """
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        ids = lambda doc: sorted(  # noqa: E731
            e.text for e in doc.iterfind(".//premis:eventIdentifierValue", NS)
        )
        assert ids(self.mets_of(sip)) == ids(premis_of(sip))
        assert ids(premis_of(sip))
        types = event_types(self.mets_of(sip))
        assert "virus check" in types and "message digest calculation" in types

    def test_every_file_has_an_amdsec_and_a_file_entry(self, source, tmp_path, monkeypatch):
        doc = self.mets_of(make_sip(source, tmp_path, monkeypatch))
        mns = {"mets": sip_mets.METS_NS}
        hrefs = sorted(
            el.get(f"{{{sip_mets.XLINK_NS}}}href")
            for el in doc.iterfind(".//mets:fileGrp[@USE='original']/mets:file/mets:FLocat", mns)
        )
        assert hrefs == ["objects/a.txt", "objects/文書/b&c.txt"]
        adm = {el.get("ADMID") for el in doc.iterfind(".//mets:file", mns)}
        assert adm == {el.get("ID") for el in doc.iterfind("mets:amdSec", mns)}

    def test_struct_map_matches_the_aip_layout(self, source, tmp_path, monkeypatch):
        """structMap は AIP の METS（core/mets.py）と同じ並び。片方だけ直すとずれる。"""
        doc = self.mets_of(make_sip(source, tmp_path, monkeypatch))
        mns = {"mets": sip_mets.METS_NS}
        sm = doc.find("mets:structMap", mns)
        assert sm.get("LABEL") == mets.STRUCT_MAP_LABEL
        outer = sm.find("mets:div", mns)
        assert outer.get("TYPE") == mets.DIV_DIRECTORY
        assert outer.get("LABEL") == doc.get("OBJID")
        assert sorted(p for p, _ in mets.physical_item_paths(doc)) == ["a.txt", "文書/b&c.txt"]

    def test_bag_also_has_mets(self, source, tmp_path, monkeypatch):
        sip = make_sip(source, tmp_path, monkeypatch, make_bag=True)
        assert self.mets_of(sip, "data").get("OBJID")

    def test_aip_moves_it_to_the_transfer_folder(self, source, tmp_path, monkeypatch):
        sip = make_sip(source, tmp_path, monkeypatch)
        sip_uuid = self.mets_of(sip).get("OBJID")
        aip = make_aip(sip, tmp_path, monkeypatch)
        subdoc = aip / "data" / "objects" / "submissionDocumentation"
        moved = subdoc / f"transfer-{sip.name}-{sip_uuid}" / "METS.xml"
        assert moved.is_file()
        assert not (subdoc / sip_mets.FILE_NAME).exists()

    def test_broken_mets_is_kept_and_reported(self, source, tmp_path, monkeypatch):
        """読めない METS は黙って捨てず、元の名前のまま残して警告を出す。"""
        sip = make_sip(source, tmp_path, monkeypatch)
        (sip / "metadata" / "submissionDocumentation" / sip_mets.FILE_NAME).write_text("<x")
        monkeypatch.undo()
        out = tmp_path / "aip-out"
        out.mkdir()
        result = aip_pipeline.run(sip_root=sip, output_parent=out, options=AIPOptions())
        subdoc = result.aip_path / "data" / "objects" / "submissionDocumentation"
        assert (subdoc / sip_mets.FILE_NAME).is_file()
        assert not list(subdoc.glob("transfer-*"))
        assert any(sip_mets.FILE_NAME in w for w in result.warnings)

    @pytest.mark.parametrize("objid", ["../../escape", "", "not-a-uuid"])
    def test_objid_must_be_a_uuid(self, objid):
        """OBJID はフォルダ名に入る。UUID の形でなければ受け付けない。

        受け付けると、手を加えた SIP から AIP の外にファイルを書かせられる。
        """
        data = f'<mets xmlns="{sip_mets.METS_NS}" OBJID="{objid}"/>'.encode()
        with pytest.raises(ValueError):
            sip_mets.read_identifier(data)


class TestViewer:
    def test_sip_shows_its_record(self, source, tmp_path, monkeypatch, fake_clamav):
        """SIP を開いたときも、処理の記録とワークフローに中身が出ること。"""
        sip = make_sip(source, tmp_path, monkeypatch, scan_virus=True)
        report = package_report.read(sip)
        assert report.overview.kind == "SIP"
        stages = {s.event_type: s for s in package_report.workflow(report)}
        assert stages["virus check"].files == 2
        assert [e.target for e in stages["virus check"].problems] == ["文書/b&c.txt"]
        assert stages["message digest calculation"].recorded

    def test_viewer_builds_for_an_old_sip(self, source, tmp_path, monkeypatch):
        from archival_packager.ui import viewer

        sip = make_sip(source, tmp_path, monkeypatch)
        (sip / "metadata" / "submissionDocumentation" / "premis.xml").unlink()
        assert package_report.read(sip).events == []
        assert viewer.build(sip, on_close=lambda: None) is not None
