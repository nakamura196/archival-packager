"""逐次書き出し版の METS が、木を丸ごと組む旧実装と同じものを出すこと。

METS は AIP に収められ、BagIt manifest のハッシュ対象になる**来歴記録**である。
メモリを削るための書き換えで出力が変われば、同じ資料から作った過去の AIP と
突き合わせられなくなる。したがって、ここで確かめるのは「妥当な XML か」ではなく
**「旧実装と 1 バイトも違わないか」**である。

旧実装は `mets._build_mets_in_memory` として残してある（参照専用）。
比較は 2 段階で行う。

1. **バイト列の完全一致。** 空白・改行・属性の並び・名前空間の宣言位置まで含めて
   同じであること。ここが本命。
2. **正規化（C14N）後の一致。** 1 が落ちたときに「意味が変わったのか、
   見た目だけなのか」を切り分けられるようにするため。1 だけだと、
   失敗したときにどちらなのか分からない。

入力は、実際の移管で出てくる組み合わせを代表として並べてある
（原本のみ／派生物あり／提出書類あり／日本語ファイル名／イベント複数）。
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

CREATED = "2026-07-25T00:00:00Z"


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


def an_event(ident: str, type_: str = "ingestion", **kw) -> PremisEvent:
    return PremisEvent(
        type=type_,
        date_time=CREATED,
        detail_note=kw.pop("detail_note", "詳細 & メモ"),
        outcome=kw.pop("outcome", "pass"),
        agent_ids=kw.pop("agent_ids", ["app"]),
        identifier=ident,
    )


def a_derivative(rel: str, uuid: str) -> Derivative:
    return Derivative(
        purpose=DerivativePurpose.PRESERVATION,
        path=Path("/x") / rel,
        relative_path=rel,
        size_bytes=99,
        uuid=uuid,
        tool_name="gs",
        command_line="gs -q ...",
        sha256="c" * 64,
        puid_out="fmt/276",
        format_name_out="Acrobat PDF/A",
    )


AGENTS = [
    PremisAgent("app", "Archival Packager", AgentKind.SOFTWARE),
    PremisAgent("org", "○○文書館", AgentKind.ORGANIZATION),
    PremisAgent("archivist", "中村 覚", AgentKind.HUMAN),
]


def _originals_only() -> dict:
    return {"files": [a_file("a.pdf", "u1"), a_file("箱01/b.pdf", "u2")], "agents": AGENTS}


def _with_derivatives() -> dict:
    f = a_file("a.eps", "u1", puid="fmt/122")
    f.derivatives = [a_derivative("a.pdf", "d1"), a_derivative("a.tif", "d2")]
    g = a_file("箱01/c.tif", "u2")
    g.derivatives = [a_derivative("箱01/c.jp2", "d3")]
    return {"files": [f, g, a_file("箱01/d.txt", "u3")], "agents": AGENTS}


def _with_submission_documents() -> dict:
    return {
        "files": [a_file("a.pdf", "u1")],
        "agents": AGENTS,
        "submission_documentation": [
            mets.SubmissionDocument(href="metadata/accession.csv", uuid="s1"),
            mets.SubmissionDocument(href="metadata/formats.csv", uuid="s2"),
        ],
    }


def _japanese_and_hostile_names() -> dict:
    names = [
        "資料/議事録 平成30年度 001.txt",
        "ぱ行とが行の濁点入り資料.txt",
        "report(final)&draft[1].txt",
        "a<b>c.pdf",
        'quote"inside.pdf',
        ("長" * 90) + ".txt",
        "2026-移管-総務課 — 第1号.txt",
    ]
    return {"files": [a_file(n, f"u{i}") for i, n in enumerate(names)], "agents": AGENTS}


def _many_events() -> dict:
    f = a_file("a.pdf", "u1")
    f.events = [
        an_event("e1", "ingestion"),
        an_event("e2", "fixity check", outcome="pass", agent_ids=["app", "org"]),
        an_event("e3", "format identification", agent_ids=["app"]),
        an_event("e4", "virus check", outcome="fail", detail_note=""),
        an_event("e5", "normalization", agent_ids=["app", "archivist", "unknown-agent"]),
    ]
    g = a_file("b.pdf", "u2")
    g.events = [an_event("e6", "ingestion")]
    return {"files": [f, g], "agents": AGENTS}


def _descriptive_everywhere() -> dict:
    f = a_file("a.pdf", "u1")
    f.descriptive = DescriptiveMetadata(title="件名 & <山田>", identifier="ID-1", language="jpn")
    g = a_file("箱01/b.pdf", "u2")
    g.descriptive = DescriptiveMetadata(description='He said "hi"')
    return {
        "files": [f, g, a_file("箱01/c.pdf", "u3")],
        "agents": AGENTS,
        "descriptive": DescriptiveMetadata(
            title="総務課移管文書", creator="総務課", date="1990-2000", extent="3 点"
        ),
    }


def _sparse_metadata() -> dict:
    """ハッシュ無し・フォーマット未識別。空要素を出していないかを見る。"""
    return {
        "files": [
            a_file("a.bin", "u1", sha256=None, puid=None, format_name=None),
            a_file("b.bin", "u2", sha256=None),
        ],
        "agents": [],
    }


def _deep_tree() -> dict:
    """並び順の固定が入れ子の中でも効いているか。"""
    names = ["z/y/x/c.txt", "z/a.txt", "z/y/b.txt", "a.txt", "z/y/x/a.txt"]
    return {"files": [a_file(n, f"u{i}") for i, n in enumerate(names)], "agents": AGENTS}


def _bulk() -> dict:
    """セクションの継ぎ目が繰り返し現れる規模。

    1 件だけでは「入れ物の開始タグ・終了タグを切り落とす」処理の誤りが、
    最初か最後の 1 回にしか現れず見逃しうる。
    """
    files = []
    for i in range(200):
        f = a_file(f"箱{i // 50:02d}/資料 {i}.txt", f"u{i}")
        f.events = [an_event(f"e{i}"), an_event(f"f{i}", "fixity check")]
        if i % 7 == 0:
            f.derivatives = [a_derivative(f"箱{i // 50:02d}/資料 {i}.pdf", f"d{i}")]
        if i % 11 == 0:
            f.descriptive = DescriptiveMetadata(title=f"件名 {i}")
        files.append(f)
    return {"files": files, "agents": AGENTS}


def _empty() -> dict:
    """ファイル 0 件。

    実際には AIP 化の前に弾かれる入力だが、空の fileGrp と空の div は
    自己閉じタグ（`<mets:fileGrp USE="original"/>`）になる。開いてから
    閉じる書き方に変えると、ここだけ形が変わる。
    """
    return {"files": [], "agents": AGENTS}


def _batch_boundary() -> dict:
    """1 ディレクトリに区切りの境目ちょうど＋1 件。

    子要素を何件かずつ区切って書き出しているので、境目の前後で
    継ぎ目が入る。ここがずれると、境目のあるデータでだけ壊れる。
    """
    n = mets._BATCH + 1
    return {"files": [a_file(f"箱/資料{i:05d}.txt", f"u{i}") for i in range(n)], "agents": AGENTS}


CASES = {
    "原本のみ": _originals_only,
    "ファイル 0 件": _empty,
    "区切りの境目": _batch_boundary,
    "派生物あり": _with_derivatives,
    "提出書類あり": _with_submission_documents,
    "日本語・特殊文字のファイル名": _japanese_and_hostile_names,
    "イベント複数": _many_events,
    "記述メタデータあり": _descriptive_everywhere,
    "ハッシュ・フォーマット不明": _sparse_metadata,
    "深い階層": _deep_tree,
    "200 件": _bulk,
}


def _kwargs(case: str) -> dict:
    kw = {
        "aip_uuid": "aip-uuid",
        "agents": [],
        "descriptive": None,
        "created_iso": CREATED,
        "submission_documentation": None,
    }
    kw.update(CASES[case]())
    return kw


@pytest.mark.parametrize("case", list(CASES))
class TestSameOutputAsTheInMemoryBuilder:
    def test_bytes_are_identical(self, case):
        """来歴記録なので、空白 1 つ違っても「変わった」とみなす。"""
        kw = _kwargs(case)
        assert mets.build_mets(**kw) == mets._build_mets_in_memory(**kw)

    def test_canonical_form_is_identical(self, case):
        """C14N でも一致すること。

        バイト一致が落ちたときに、意味が変わったのか見た目だけなのかを
        切り分けられるようにするため、別の検査として置いてある。
        """
        kw = _kwargs(case)
        new = etree.canonicalize(mets.build_mets(**kw).decode("utf-8"))
        old = etree.canonicalize(mets._build_mets_in_memory(**kw).decode("utf-8"))
        assert new == old


@pytest.mark.parametrize("case", list(CASES))
def test_write_mets_writes_the_same_bytes(case, tmp_path: Path):
    """ファイルへ直接流した場合も同じバイト列になること。

    数万件では、バイト列を戻り値で受け取ること自体がメモリの下限になる。
    その逃げ道として `write_mets` を用意してあるので、同じものが出ることを
    固定しておく（呼び出し元を切り替えるときの安全網）。
    """
    kw = _kwargs(case)
    out = tmp_path / "METS.xml"
    with out.open("wb") as fh:
        mets.write_mets(fh, **kw)
    assert out.read_bytes() == mets.build_mets(**kw)


def test_ids_stay_consistent_across_sections():
    """逐次化で最も危ういのは ID の導き直し。

    amdSec を書き終えた時点でその木は捨てているので、fileSec は admID を
    並び順から導き直している。ずれると ADMID が実在しない amdSec を指す。
    """
    ns = {"mets": mets.METS_NS}
    root = etree.fromstring(mets.build_mets(**_kwargs("派生物あり")))
    adm_ids = set(root.xpath("//mets:amdSec/@ID", namespaces=ns))
    file_ids = set(root.xpath("//mets:file/@ID", namespaces=ns))

    admids_in_use = root.xpath("//mets:file/@ADMID", namespaces=ns)
    assert admids_in_use, "ADMID が 1 つも無いなら、この検査は何も見ていない"
    for admid in admids_in_use:
        assert admid in adm_ids

    for fid in root.xpath("//mets:fptr/@FILEID", namespaces=ns):
        assert fid in file_ids


def _largest_tree_built(n_files: int) -> int:
    """METS を 1 本作る間に、1 度に組まれた最大の要素数。"""
    biggest = 0
    original = mets._SectionSink.section_bytes

    def watched(self, holder):
        nonlocal biggest
        biggest = max(biggest, sum(1 for _ in holder.iter()))
        return original(self, holder)

    files = [a_file(f"箱{i // 100:04d}/資料 {i}.txt", f"u{i}") for i in range(n_files)]
    mets._SectionSink.section_bytes = watched
    try:
        mets.build_mets(
            aip_uuid="aip-uuid", files=files, agents=AGENTS, descriptive=None,
            created_iso=CREATED, submission_documentation=None,
        )
    finally:
        mets._SectionSink.section_bytes = original
    return biggest


def test_the_tree_built_at_once_does_not_grow_with_the_file_count():
    """**これがメモリを削った本体である。**

    出力が同じかどうかだけを見ていると、木を丸ごと組む実装に戻しても
    テストは全部通ってしまう。削ったのは「1 度に組む木の大きさ」なので、
    そこを直接押さえる。件数を 4 倍にしても、1 度に組む要素数が増えないこと
    （機械の速さやメモリ量に依存しないので、CI でも意味がある）。
    """
    small = _largest_tree_built(750)
    large = _largest_tree_built(3000)

    assert small == large, "1 度に組む木が件数で変わるなら、逐次化が効いていない"
    # fileSec / structMap は 1 件あたり要素 2 つを _BATCH 件ずつ組む。
    # 親の入れ子の分だけ少し足しても、この上限に収まる。
    assert large <= 2 * mets._BATCH + 16
