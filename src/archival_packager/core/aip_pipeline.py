"""AIP 生成パイプライン（読み取り → 完全性確認 → 正規化 → METS → bag 化）。

現行 Swift 実装の `Sources/AIP/AIPOrchestrator.swift` と `AIPBuilder.swift` に対応する。

    入力 = SIP Creator が出した SIP ディレクトリ または BagIt bag
    出力 = Archivematica 風 AIP（BagIt bag、data/METS.<uuid>.xml に PREMIS を埋める）

AIP の data/ 配下:

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

from .. import __version__
from . import (
    conversion_registry,
    fixity,
    mets,
    normalizer,
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

    work_dir = Path(tempfile.mkdtemp(prefix="archival-packager-normalize-"))
    try:
        warnings: list[str] = []
        if options.normalize:
            warnings += _normalize_all(parsed.files, work_dir, now, progress)
        else:
            progress("フォーマット変換は行いません（オプション OFF）")

        aip_uuid = str(_uuid.uuid4())
        descriptive = DescriptiveMetadata.merge(options.descriptive, parsed.descriptive)

        progress("METS を生成しています…")
        mets_xml = mets.build_mets(
            aip_uuid=aip_uuid,
            files=parsed.files,
            agents=agents,
            descriptive=descriptive,
            created_iso=now,
            submission_documentation=_submission_documents(parsed),
        )

        progress("AIP（BagIt bag）を組み立てています…")
        result = _build(
            sip_root=sip_root,
            parsed=parsed,
            output_parent=output_parent,
            aip_uuid=aip_uuid,
            mets_xml=mets_xml,
            fixity_status=fixity_status,
            warnings=warnings,
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


def _submission_documents(parsed) -> list[mets.SubmissionDocument]:
    """AIP に継承する提出書類を、METS に載せる形で並べる。

    **AIP に入れているのに fileSec に無いと、METS だけを読む側からは
    存在しないことになる。** BagIt のマニフェストには入っていたが、
    METS からは辿れていなかった。
    """
    root = parsed.submission_documentation
    if not root or not root.is_dir():
        return []
    docs: list[mets.SubmissionDocument] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        docs.append(
            mets.SubmissionDocument(
                href=f"objects/submissionDocumentation/{rel}",
                uuid=str(_uuid.uuid4()),
            )
        )
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
                    detail_note=exc.message, outcome="fail", agent_ids=[APP_AGENT_ID],
                )
            )
            continue

        f.derivatives.append(derivative)
        f.events.append(
            PremisEvent(
                type="normalization", date_time=now,
                detail_note=derivative.command_line, outcome="success",
                agent_ids=[APP_AGENT_ID],
            )
        )

    return warnings


def _build(
    *,
    sip_root: Path,
    parsed: sip_reader.ParsedSIP,
    output_parent: Path,
    aip_uuid: str,
    mets_xml: bytes,
    fixity_status: FixityStatus,
    warnings: list[str],
) -> AIPResult:
    import bagit

    aip_dir = _make_aip_dir(sip_root, output_parent)
    objects = aip_dir / "objects"
    logs = aip_dir / "logs"
    objects.mkdir(parents=True)
    logs.mkdir(parents=True)

    for f in parsed.files:
        _copy(f.absolute_path, objects / f.relative_path)
        for d in f.derivatives:
            _copy(d.path, objects / d.relative_path)

    # 提出書類の継承。SIP 段で作った記録を AIP に持ち込む。
    if parsed.submission_documentation and parsed.submission_documentation.is_dir():
        dest = objects / "submissionDocumentation"
        shutil.copytree(parsed.submission_documentation, dest, dirs_exist_ok=True)

    (logs / "README.txt").write_bytes(
        "AIP 保存処理ログ（将来: 正規化・検証の詳細）。\n".encode()
    )

    mets_name = f"METS.{aip_uuid}.xml"
    (aip_dir / mets_name).write_bytes(mets_xml)

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
