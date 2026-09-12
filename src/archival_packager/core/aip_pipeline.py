"""AIP 生成パイプライン（読み取り → 完全性確認 → 正規化 → METS → bag 化）。

退役した Swift 実装の `Sources/AIP/AIPOrchestrator.swift` と `AIPBuilder.swift` に由来する。

    入力 = SIP Creator が出した SIP ディレクトリ または BagIt bag
    出力 = Archivematica 風 AIP（BagIt bag、data/METS.<uuid>.xml に PREMIS を埋める）

AIP の data/ 配下:

    README.html                         人向けの案内（日本語・英語）
    objects/                            原本（相対パス保持）＋ 派生物
    objects/submissionDocumentation/    SIP 段の提出書類を継承
    logs/                               保存処理ログ
    METS.<aip-uuid>.xml                 amdSec に PREMIS を埋めた METS
"""

from __future__ import annotations

import shutil
import tempfile
import uuid as _uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from .. import __version__
from . import (
    conversion_registry,
    fixity,
    mets,
    normalizer,
    rule_table,
    sip_builder,
    sip_reader,
    zip_io,
)
from .aip_models import (
    AgentKind,
    AIPErrorKind,
    AIPFile,
    AIPOptions,
    AIPPipelineError,
    AIPResult,
    CheckOutcome,
    Derivative,
    DerivativePurpose,
    DescriptiveMetadata,
    FixityOutcome,
    FixityStatus,
    PremisAgent,
    PremisEvent,
)

Progress = Callable[[str], None]

APP_AGENT_ID = "archival-packager"
#: PREMIS の agentName。版を直書きしない（dfxml.py の注を参照）。
APP_AGENT_NAME = f"Archival Packager {__version__}"


def run(
    *,
    sip_root: Path,
    output_parent: Path,
    options: AIPOptions,
    progress: Progress = lambda _msg: None,
) -> AIPResult:
    is_bag = sip_reader.detect_bag(sip_root)
    progress(f"入力を読み取っています（{'BagIt bag' if is_bag else 'SIP ディレクトリ'}）…")

    parsed = sip_reader.read(sip_root, is_bag=is_bag)
    progress(
        f"原本 {len(parsed.files)} 件"
        f"（ハッシュ継承 {parsed.inherited_hashes} 件 / 再計算 {parsed.recomputed_hashes} 件）"
    )

    progress("完全性を確認しています（マニフェスト照合）…")
    fixity_status = fixity.verify(sip_root, is_bag=is_bag)
    progress(_fixity_message(fixity_status))

    now = _now_iso()
    agents = _agents(options)
    _record_ingestion_events(parsed.files, fixity_status, now, options)

    # 規則表は、変換を始める前に 1 回だけ確定させる。**AIP に同梱するのは
    # 「実際に効いていた表」でなければならない**ので、参照するのは常にこの文字列。
    rules_document = conversion_registry.document()

    work_dir = Path(tempfile.mkdtemp(prefix="archival-packager-normalize-"))
    try:
        # 表の不備は利用者にしか直せない。黙って捨てると、書いたはずの規則が
        # 効かない理由が誰にも分からなくなる。変換の有無にかかわらず出す。
        warnings: list[str] = [f"変換規則表: {w}" for w in conversion_registry.warnings()]
        if options.normalize:
            warnings += _normalize_all(parsed.files, work_dir, now, progress)
        else:
            progress("フォーマット変換は行いません（オプション OFF）")

        aip_uuid = str(_uuid.uuid4())
        descriptive = DescriptiveMetadata.merge(options.descriptive, parsed.descriptive)

        # **バイト列にしてから渡さない。** 5 万件の METS は 300MB 近くになり、
        # 戻り値として持つだけでそのぶん常駐する（実測 283.8MiB）。
        # 書き出し先が決まる `_build` の中で、直接ファイルへ流す。
        def write_mets(out) -> None:
            mets.write_mets(
                out,
                aip_uuid=aip_uuid,
                files=parsed.files,
                agents=agents,
                descriptive=descriptive,
                created_iso=now,
                submission_documentation=_submission_documents(parsed),
            )

        result = _build(
            sip_root=sip_root,
            parsed=parsed,
            output_parent=output_parent,
            aip_uuid=aip_uuid,
            write_mets=write_mets,
            fixity_status=fixity_status,
            warnings=warnings,
            rules_document=rules_document,
            progress=progress,
        )

        if options.serialize_zip:
            progress("ZIP（無圧縮）に固めています…")
            result.zip_path = zip_io.create_stored(result.aip_path)

        progress("完了しました。")
        return result

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# --------------------------------------------------------------------------
# 各段
# --------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fixity_message(status: FixityStatus) -> str:
    if status.outcome is FixityOutcome.PASSED:
        return f"完全性確認: {status.checked} 件すべて一致"
    if status.outcome is FixityOutcome.FAILED:
        return f"完全性確認: {len(status.mismatches)} 件の不一致。要確認"
    return f"完全性確認をスキップ: {status.reason}"


def _agents(options: AIPOptions) -> list[PremisAgent]:
    agents = [PremisAgent(APP_AGENT_ID, APP_AGENT_NAME, AgentKind.SOFTWARE)]
    if options.archivist_name:
        agents.append(PremisAgent(options.archivist_name, options.archivist_name, AgentKind.HUMAN))
    return agents


#: AIP に同梱する規則表の置き場所（data/ からの相対）。
RULES_DOCUMENT_HREF = f"objects/submissionDocumentation/{rule_table.DOCUMENT_NAME}"


def _submission_documents(parsed) -> list[mets.SubmissionDocument]:
    """AIP に継承する提出書類を、METS に載せる形で並べる。

    **AIP に入れているのに fileSec に無いと、METS だけを読む側からは
    存在しないことになる。** BagIt のマニフェストには入っていたが、
    METS からは辿れていなかった。

    ここで書き出す規則表も同じ扱いにする（_build が実体を書く）。
    """
    docs: list[mets.SubmissionDocument] = []
    root = parsed.submission_documentation
    if root and root.is_dir():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            rel = path.relative_to(root).as_posix()
            href = f"objects/submissionDocumentation/{rel}"
            if href == RULES_DOCUMENT_HREF:
                # AIP を入力にして作り直した場合、継承元に同じ名前の表がある。
                # 二重に載せず、今回の表（下で足す方）で置き換える。
                continue
            docs.append(mets.SubmissionDocument(href=href, uuid=str(_uuid.uuid4())))

    docs.append(mets.SubmissionDocument(href=RULES_DOCUMENT_HREF, uuid=str(_uuid.uuid4())))
    return docs


def _record_ingestion_events(
    files: list[AIPFile], status: FixityStatus, now: str, options: AIPOptions
) -> None:
    """各原本に ingestion と fixity check の event を記録する。"""
    agent_ids = [APP_AGENT_ID]
    if options.archivist_name:
        agent_ids.append(options.archivist_name)

    if status.outcome is FixityOutcome.PASSED:
        outcome, note = "pass", f"マニフェストと一致（{status.checked} 件中）"
    elif status.outcome is FixityOutcome.FAILED:
        outcome, note = "fail", f"不一致 {len(status.mismatches)} 件"
    else:
        outcome, note = "skipped", status.reason

    for f in files:
        f.events.append(
            PremisEvent(
                type="ingestion", date_time=now, detail_note="AIP 化のため取り込み",
                outcome="success", agent_ids=agent_ids,
            )
        )
        f.events.append(
            PremisEvent(
                type="fixity check", date_time=now, detail_note=note,
                outcome=outcome, agent_ids=agent_ids,
            )
        )
        # **識別と検査も保存処理である。** 実行したのに記録していなかった。
        # PREMIS は、いつ・何を・どの道具で行ったかを残すためにある。
        _append_identification_event(f, now, agent_ids)
        _append_virus_event(f, now, agent_ids)


#: フォーマット識別に使っている道具。PREMIS の linkingAgent に出す。
IDENTIFICATION_AGENT = "Siegfried (PRONOM)"


def _append_identification_event(f: AIPFile, now: str, agent_ids: list[str]) -> None:
    """フォーマット識別の event。

    識別そのものは SIP を作るときに行われ、その結果が技術インベントリに
    残っている。AIP 化にあたって、その事実を保存処理記録として書き出す。
    """
    if f.puid:
        outcome = "success"
        note = f"PRONOM {f.puid}" + (f"（{f.format_name}）" if f.format_name else "")
    else:
        # 識別できなかったことも記録に値する。あとから見た人が、
        # 「識別しなかった」のか「識別できなかった」のかを区別できる。
        outcome = "fail"
        note = "識別できませんでした"
    f.events.append(
        PremisEvent(
            type="format identification", date_time=now, detail_note=note,
            outcome=outcome, agent_ids=[*agent_ids, IDENTIFICATION_AGENT],
        )
    )


def _append_virus_event(f: AIPFile, now: str, agent_ids: list[str]) -> None:
    """ウイルス検査の event。**実施していないときは書かない。**

    「検査して検出なし」と「検査していない」を取り違えられては困る。
    書かないことが「不明」を表す。
    """
    state = (f.virus_state or "").strip()
    if not state or state.startswith("未実施"):
        return
    if state.startswith("検出:"):
        outcome, note = "fail", state
    else:
        outcome, note = "pass", state
    f.events.append(
        PremisEvent(
            type="virus check", date_time=now, detail_note=note,
            outcome=outcome, agent_ids=[*agent_ids, VIRUS_AGENT],
        )
    )


#: ウイルス検査に使っている道具。
VIRUS_AGENT = "ClamAV"


# 同梱していない変換ツールについて、report を読んだ人が次に何をすればよいか。
_TOOL_HINTS = {
    # AGPL-3.0 のため同梱していない。判断の経緯は conversion_registry.py と
    # scripts/fetch-binaries.zsh の冒頭に書いてある。
    "gs": "Ghostscript は同梱していません。PATH 上にあれば使います",
}


def _tool_hint(tool: str) -> str:
    return _TOOL_HINTS.get(tool, "同梱されていません")


def _normalize_all(
    files: list[AIPFile], work_dir: Path, now: str, progress: Progress
) -> list[str]:
    """正規化可能なファイルに派生物を作る。失敗は警告にして続行する。"""
    warnings: list[str] = []
    targets = [
        (f, rule)
        for f in files
        if (rule := conversion_registry.rule_for(f.puid, DerivativePurpose.PRESERVATION))
    ]

    if not targets:
        progress("正規化の対象はありません（既に保存に適した形式、または未知の形式）")
        return warnings

    progress(f"フォーマット変換: {len(targets)} 件")
    for f, rule in targets:
        try:
            derivative = normalizer.normalize(f, rule, work_dir)
        except AIPPipelineError as exc:
            # 1 ファイルの変換失敗で移管全体を止めない。原本はそのまま保存される。
            #
            # 「ツールが無い」と「ファイルが変換できない」は原因も対処も違う。
            # 前者は環境の問題（同梱していない Ghostscript が代表例）で、
            # 資料そのものには何も問題がない。同じ文言にすると、資料が壊れて
            # いるのかツールが足りないのか、report を読んでも区別できない。
            if exc.kind is AIPErrorKind.TOOL_NOT_FOUND:
                note = (
                    f"変換ツールが無いため原本のまま保存: {f.relative_path} "
                    f"— {rule.tool}（{_tool_hint(rule.tool)}）"
                )
            else:
                note = f"変換に失敗（原本のまま保存）: {f.relative_path} — {exc.message}"
            warnings.append(note)
            progress(note)
            f.events.append(
                PremisEvent(
                    type="normalization", date_time=now,
                    # 失敗のときも、どの規則が動こうとしたのかは残す。
                    # 「変換されなかった」だけでは、規則が無かったのか
                    # 規則はあったが失敗したのかを後から区別できない。
                    detail_note=f'rule="{rule.rule_id}"; {exc.message}',
                    outcome="fail", agent_ids=[APP_AGENT_ID],
                )
            )
            continue

        # 作ったものを開き直せたか。**開けなければ成果物に入れない。**
        # 中途半端な派生物を保存用として記録するほうが、変換できなかったと
        # 記録するより悪い（後から見た人が「保存用がある」と信じてしまう）。
        kept = derivative.check is None or derivative.check.outcome is not CheckOutcome.FAILED
        detail = normalizer.event_detail(derivative)

        if kept:
            f.derivatives.append(derivative)
        else:
            note = (
                f"変換結果を読み戻せないため原本のまま保存: {f.relative_path} "
                f"— {derivative.check.note}"
            )
            warnings.append(note)
            progress(note)
            detail += "; 読み戻せなかったため破棄しました"

        f.events.append(
            PremisEvent(
                type="normalization", date_time=now,
                # どの規則が・どの道具の・どの版で動いたか
                #（Archivematica の eventDetail に倣う）。
                detail_note=detail, outcome="success" if kept else "fail",
                agent_ids=[APP_AGENT_ID],
            )
        )
        _append_validation_event(f, derivative, now)

    return warnings


#: PREMIS の eventOutcome に書く値。CheckOutcome の綴りをそのまま出さないのは、
#: 既に出している fixity check / virus check が pass / fail / skipped だから
#: （同じ METS の中で語彙が揺れると、読む側が別物と受け取る）。
_CHECK_OUTCOMES = {
    CheckOutcome.PASSED: "pass",
    CheckOutcome.FAILED: "fail",
    CheckOutcome.SKIPPED: "skipped",
}


def _append_validation_event(f: AIPFile, derivative: Derivative, now: str) -> None:
    """派生物を開き直した結果を PREMIS の event として残す。

    **eventType は PREMIS の語彙どおり "validation"。** 独自の名前を付けると、
    他のシステムがこの AIP を読んだときに何の記録か分からなくなる。

    **成功も失敗も、確認しなかったことも書く。** 「確認していない」と
    「確認して通った」は別の事実である。イベントが無いことで「不明」を表すと、
    後から見た人はまず区別できない（ウイルス検査は実施の有無が SIP 側の
    記録から分かるので書かない、という別の判断をしている）。

    ただしここで言う validation は **読み戻せたかどうかだけ** であり、
    veraPDF や JHOVE のような形式適合性の判定ではない。何を見たかが
    eventOutcomeDetailNote から読み取れるように、道具の名前を note に入れる。
    """
    check = derivative.check
    if check is None:
        return
    agent_ids = [APP_AGENT_ID]
    if check.agent:
        agent_ids.append(check.agent)
    f.events.append(
        PremisEvent(
            type="validation", date_time=now,
            detail_note=f"{derivative.relative_path}: {check.note}",
            outcome=_CHECK_OUTCOMES[check.outcome], agent_ids=agent_ids,
        )
    )


def _build(
    *,
    sip_root: Path,
    parsed: sip_reader.ParsedSIP,
    output_parent: Path,
    aip_uuid: str,
    write_mets: Callable[[BinaryIO], None],
    fixity_status: FixityStatus,
    warnings: list[str],
    rules_document: str,
    progress: Progress,
) -> AIPResult:
    import bagit

    aip_dir = _make_aip_dir(sip_root, output_parent)
    objects = aip_dir / "objects"
    logs = aip_dir / "logs"
    objects.mkdir(parents=True)
    logs.mkdir(parents=True)

    # 進捗の表示と実際の処理の順序を合わせる。METS の生成はここで起きるので、
    # 呼び出し元で先に「生成しています」と出すと、実態より早く出てしまう。
    progress("METS を生成しています…")
    mets_name = f"METS.{aip_uuid}.xml"
    with (aip_dir / mets_name).open("wb") as fh:
        write_mets(fh)

    progress("AIP（BagIt bag）を組み立てています…")

    for f in parsed.files:
        _copy(f.absolute_path, objects / f.relative_path)
        for d in f.derivatives:
            _copy(d.path, objects / d.relative_path)

    # 提出書類の継承。SIP 段で作った記録を AIP に持ち込む。
    if parsed.submission_documentation and parsed.submission_documentation.is_dir():
        dest = objects / "submissionDocumentation"
        shutil.copytree(parsed.submission_documentation, dest, dirs_exist_ok=True)

    # **使った規則表そのものを同梱する。** Archivematica は PREMIS に FPR の
    # 識別子だけを書き、規則の中身は中央の登録簿にある。後年その登録簿が
    # 引けなくなると、「どの規則で作られたか」が書いてあっても意味を失う。
    # 表を一緒に入れておけば、このパッケージ単体で説明が付く。
    # 継承した提出書類のコピーより後に書くこと（同名ファイルを上書きして、
    # METS に載せた今回の表と実体を一致させる）。
    rules_doc = aip_dir / RULES_DOCUMENT_HREF
    rules_doc.parent.mkdir(parents=True, exist_ok=True)
    rules_doc.write_text(rules_document, encoding="utf-8")

    (logs / "README.txt").write_bytes(
        "AIP 保存処理ログ（将来: 正規化・検証の詳細）。\n".encode()
    )

    # **人が最初に開くファイルを、パッケージの一番上に置く。** ここまでの
    # 説明はすべて METS / PREMIS の中にあり、XML を読める人にしか届かない。
    # 10 年後にこの bag を渡された人が、まず何を見ればよいかを知る手がかりを
    # 平文で残す。Archivematica の AIP も data/README.html を置いている。
    # bagit.make_bag より前に書くので、BagIt の payload に入り manifest に載る。
    (aip_dir / "README.html").write_text(_readme_html(aip_uuid), encoding="utf-8")

    try:
        bagit.make_bag(str(aip_dir), bag_info={"External-Identifier": aip_uuid}, checksums=["sha256"])
    except Exception as exc:
        raise AIPPipelineError.io(f"bag 化に失敗: {exc}") from exc

    all_warnings = list(warnings)
    if fixity_status.outcome is FixityOutcome.FAILED:
        head = ", ".join(fixity_status.mismatches[:5])
        all_warnings.append(
            f"完全性確認で {len(fixity_status.mismatches)} 件の不一致。AIP 化を続行したが要確認: {head}"
        )
    elif fixity_status.outcome is FixityOutcome.SKIPPED:
        all_warnings.append(f"完全性確認をスキップ: {fixity_status.reason}")

    all_warnings += [f"未識別フォーマット: {f.relative_path}" for f in parsed.files if f.puid is None]

    return AIPResult(
        aip_path=aip_dir,
        aip_uuid=aip_uuid,
        mets_path=aip_dir / "data" / mets_name,
        original_count=len(parsed.files),
        derivative_count=sum(len(f.derivatives) for f in parsed.files),
        fixity=fixity_status,
        warnings=all_warnings,
    )


#: data/README.html の中身。
#:
#: **日本語と英語の両方を書く。** 画面表示の言語はアプリの設定で変わるが、
#: パッケージの中身は作った環境から切り離されて流通する。受け取った人が
#: どちらの言語で読むかは、こちらから決められない。
#:
#: **外部の CSS も画像も参照しない。** ネットワークの無い場所で、ブラウザに
#: ファイルを放り込んだだけで読めること。参照先はいずれ必ず消える。
#:
#: 内容は「これは何か・何が入っているか・どう読むか」に絞る。記述メタデータを
#: ここへ複写しない。**同じ事実を 2 か所に書くと、いずれ食い違う**（正本は
#: METS の dmdSec であり、ここはその入口を案内するだけ）。
_README_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>About this package / このパッケージについて</title>
<style>
body {{ font-family: sans-serif; line-height: 1.7; margin: 2em auto; max-width: 46em; padding: 0 1em; }}
code {{ background: #f2f2f2; padding: 0 .3em; }}
table {{ border-collapse: collapse; }}
th, td {{ border: 1px solid #ccc; padding: .3em .8em; text-align: left; vertical-align: top; }}
hr {{ margin: 2.5em 0; border: 0; border-top: 1px solid #ccc; }}
</style>
</head>
<body>

<h1>このパッケージについて</h1>

<p>これは <strong>AIP（長期保存用情報パッケージ）</strong> です。
OAIS 参照モデルにいう保存用のひとまとまりで、資料そのものと、
それに何をしたかの記録が同じ入れ物に入っています。
<a href="https://github.com/nakamura196/archival-packager">Archival Packager</a> が作りました。</p>

<p>このパッケージの識別子: <code>{aip_uuid}</code></p>

<h2>何が入っているか</h2>

<table>
<tr><th><code>data/objects/</code></th>
    <td>資料そのもの。元のフォルダ構成のまま入っています。</td></tr>
<tr><th><code>data/objects/*-preservation.*</code></th>
    <td>長期保存に向いた形式へ変換した複製（例: PNG から TIFF）。
        <strong>原本は変更していません。</strong>変換していない資料にはこれがありません。</td></tr>
<tr><th><code>data/objects/submissionDocumentation/</code></th>
    <td>受入時に作った記録（ファイル一覧、フォーマット識別の結果、変換規則の表など）。</td></tr>
<tr><th><code>data/logs/</code></th>
    <td>保存処理のログ。</td></tr>
<tr><th><code>data/METS.{aip_uuid}.xml</code></th>
    <td>目録。資料の記述、ファイルの一覧、そして
        <strong>いつ・何を・どの道具の何版で行ったか</strong>（PREMIS）が入っています。</td></tr>
<tr><th><code>manifest-sha256.txt</code></th>
    <td>全ファイルの SHA-256。中身が変わっていないことを後から確かめるために使います。</td></tr>
</table>

<h2>どう読めばよいか</h2>

<ol>
<li>資料を見るだけなら <code>data/objects/</code> をそのまま開いてください。
    特別なソフトは要りません。</li>
<li>中身が壊れていないか確かめるには、この入れ物が <strong>BagIt</strong> という
    仕様に従っているので、BagIt 対応のツール（<code>bagit.py</code> など）で
    検証できます。手元にツールが無くても、
    <code>manifest-sha256.txt</code> の各行と実ファイルの SHA-256 を
    突き合わせれば同じことができます。</li>
<li>何が行われたかを知るには <code>data/METS.{aip_uuid}.xml</code> を開き、
    <code>premis:event</code> を読んでください。取り込み・完全性確認・
    フォーマット識別・ウイルス検査・形式変換・変換結果の読み戻し確認が、
    ファイルごとに記録されています。</li>
</ol>

<h2>注意</h2>

<p>変換結果については「保存用に作ったファイルを開き直せたか」までを確認しています。
<strong>形式の仕様に適合しているかどうかの検査（veraPDF や JHOVE に相当するもの）は
行っていません。</strong>記録の読み方を誤らないよう、ここに明記しておきます。</p>

<hr>

<h1>About this package</h1>

<p>This is an <strong>AIP (Archival Information Package)</strong> in the sense of the
OAIS reference model: the records themselves, together with the account of what was
done to them, in one container. It was produced by
<a href="https://github.com/nakamura196/archival-packager">Archival Packager</a>.</p>

<p>Package identifier: <code>{aip_uuid}</code></p>

<h2>What is inside</h2>

<table>
<tr><th><code>data/objects/</code></th>
    <td>The records, in their original directory structure.</td></tr>
<tr><th><code>data/objects/*-preservation.*</code></th>
    <td>Copies converted to a preservation format (for example PNG to TIFF).
        <strong>The originals were never modified.</strong> Records that were not
        converted have no such copy.</td></tr>
<tr><th><code>data/objects/submissionDocumentation/</code></th>
    <td>Documentation created at accession: file inventory, format identification
        results, and the normalisation rule table that was in force.</td></tr>
<tr><th><code>data/logs/</code></th>
    <td>Preservation processing logs.</td></tr>
<tr><th><code>data/METS.{aip_uuid}.xml</code></th>
    <td>The catalogue: description, file inventory, and
        <strong>what was done, when, and with which version of which tool</strong> (PREMIS).</td></tr>
<tr><th><code>manifest-sha256.txt</code></th>
    <td>SHA-256 of every file, so that the contents can be checked later.</td></tr>
</table>

<h2>How to read it</h2>

<ol>
<li>To look at the records, open <code>data/objects/</code>. No special software
    is needed.</li>
<li>To check that nothing has changed, note that this container follows the
    <strong>BagIt</strong> specification; any BagIt tool (such as
    <code>bagit.py</code>) will validate it. Without a tool, comparing each line of
    <code>manifest-sha256.txt</code> against the SHA-256 of the file achieves the same.</li>
<li>To see what was done, open <code>data/METS.{aip_uuid}.xml</code> and read the
    <code>premis:event</code> elements: ingestion, fixity check, format
    identification, virus check, normalisation, and the read-back check of each
    converted file are recorded per file.</li>
</ol>

<h2>A caution</h2>

<p>For converted files, we checked only that the generated copy can be opened again.
<strong>No format conformance validation (the equivalent of veraPDF or JHOVE) was
performed.</strong> This is stated here so that the records are not read as claiming
more than they do.</p>

</body>
</html>
"""


def _readme_html(aip_uuid: str) -> str:
    """人向けの README.html を組む。**METS のファイル名を案内するので UUID が要る。**"""
    return _README_HTML.format(aip_uuid=aip_uuid)


def _make_aip_dir(sip_root: Path, output_parent: Path) -> Path:
    base = f"{sip_root.name}-AIP"
    candidate = output_parent / base
    counter = 2
    while candidate.exists():
        candidate = output_parent / f"{base} {counter}"
        counter += 1
    try:
        candidate.mkdir(parents=True)
    except OSError as exc:
        raise AIPPipelineError.io(f"ディレクトリ作成に失敗: {candidate} ({exc})") from exc
    return candidate


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        raise AIPPipelineError.io(f"コピーに失敗: {src} ({exc})") from exc


def validate_aip(aip_path: Path) -> None:
    """生成した AIP が BagIt 仕様に適合しているか検証する。"""
    sip_builder.validate_bag(aip_path)
