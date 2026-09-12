"""コマンドライン入口（**ソースから動かす利用者向け**）。

なぜ要るか
----------
画面からしか使えないと、毎晩の受入や CI での退行検知を機械にやらせられない。
文書館側から「サーバを立てずに移管処理を自動化したい」という要望が出ている。

**配布物（.app / MSIX）では使えない。** 包んだアプリには起動時の引数が届かず、
実測で ``argv=['']`` になる（``ui/app.py`` の ``SELF_TEST_ENV`` と ``self_test()``
の注を参照。自己診断が引数ではなく環境変数で切り替わるのはこのため）。
したがってこの入口はソースから動かす利用者のためのものであり、
**配布物向けの CLI は作らない**。作っても引数が届かないので動かせない。

設計
----
``core.sip_pipeline.run()`` / ``core.aip_pipeline.run()`` は画面に依存しない
（引数とコールバックだけで動く）。ここはその上に**薄くかぶせるだけ**にする。
受入の判断をこちらに書くと、画面と CLI で振る舞いが分かれ、
「どちらの結果が正なのか」を後から誰も言えなくなる。

  進捗 → 標準エラー / 結果 → 標準出力
      ``--json`` のときの標準出力を JSON 1 個だけに保つため。
      こうしておくと ``| jq`` にそのまま繋がる。

  終了コード  0 = 成功 / 1 = 処理の失敗 / 2 = 引数の誤り
      **ウイルスの検出と個人情報の候補は失敗にしない。** 検出は人が判断する
      ための材料であって、処理そのものは成功している。ここを 1 にすると、
      毎晩の自動処理が「止めるべき失敗」と「見てほしい所見」を区別できなくなる。
      見落とされては困るので、標準エラーと ``--json`` の結果には必ず出す。

      存在しないパスを渡された場合は 2（引数の誤り）にする。処理を始める前に
      分かることであり、直すのは渡した側だから。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .core import (
    aip_pipeline,
    bundled,
    clamav,
    conversion_registry,
    package_report,
    rule_table,
    sip_pipeline,
    zip_io,
)
from .core.aip_models import AIPOptions, AIPPipelineError
from .core.models import SIPMetadata, SIPOptions, SIPPipelineError

PROG = "archival-packager"

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

#: 人が読む出力でファイル一覧をそのまま並べる上限。
#: 5 万件の SIP でも動くアプリなので、端末に全部流すと読めるものにならない。
#: ``--json`` のほうは機械が読むので全件出す。
INSPECT_FILE_PREVIEW = 20


class _UsageError(Exception):
    """引数の誤り（終了コード 2）。渡した側が直せるもの。"""


class _RunError(Exception):
    """処理の失敗（終了コード 1）。入力や環境に起因するもの。"""


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------


class _Reporter:
    """どこへ何を書くかを 1 箇所に閉じ込める。

    進捗を標準出力に混ぜると ``--json`` がパイプで使えなくなる。
    「うっかり print した」を防ぐため、書き出しはすべてここを通す。
    """

    def __init__(self, *, as_json: bool = False, quiet: bool = False) -> None:
        self.as_json = as_json
        self.quiet = quiet

    def progress(self, message: str) -> None:
        """進捗。``--quiet`` で止まる。標準出力には決して書かない。"""
        if not self.quiet:
            print(message, file=sys.stderr, flush=True)

    def notice(self, message: str) -> None:
        """見落とされては困る知らせ（検出・警告・失敗）。``--quiet`` でも出す。

        黙って進むことをこのアプリは許していない（CLAUDE.md の
        「Silent failure is not acceptable」）。静かにしてよいのは進捗だけ。
        """
        print(message, file=sys.stderr, flush=True)

    def emit(self, payload: dict[str, Any], lines: Sequence[str]) -> None:
        """結果を標準出力へ。JSON のときは 1 個の値だけを書く。"""
        if self.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for line in lines:
                print(line)

    def emit_error(self, command: str, message: str) -> None:
        """失敗も ``--json`` なら JSON 1 個で返す（パイプの先で分岐できるように）。"""
        self.notice(f"エラー: {message}")
        if self.as_json:
            print(
                json.dumps(
                    {"command": command, "status": "error", "message": message},
                    ensure_ascii=False,
                    indent=2,
                )
            )


# --------------------------------------------------------------------------
# 引数
# --------------------------------------------------------------------------


#: argparse 自身が出す英語の言い回しを日本語にする。
#: 利用者に見せる文字はすべて日本語で揃える（画面がそうなので）。
_ARGPARSE_PHRASES = (
    ("the following arguments are required:", "次の引数が足りません:"),
    ("unrecognized arguments:", "知らない引数です:"),
    ("expected one argument", "には値が必要です"),
    ("invalid choice:", "は選べません:"),
    ("argument ", "引数 "),
)


class _Parser(argparse.ArgumentParser):
    """引数の誤りを日本語で伝え、終了コード 2 で終わる。

    既定の argparse は英語で終了コード 2。コードは合っているが、文言が
    英語のままだと「何を直せばよいか」が利用者に伝わらない。
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        translated = message
        for english, japanese in _ARGPARSE_PHRASES:
            translated = translated.replace(english, japanese)
        self.exit(
            EXIT_USAGE,
            f"エラー: {translated}\n"
            f"  使い方を見るには: {self.prog} --help\n",
        )


def build_parser() -> _Parser:
    parser = _Parser(
        prog=PROG,
        description=(
            "OAIS の情報パッケージ（SIP / AIP）をコマンドラインから作ります。"
            "配布版（.app / MSIX）では使えません（引数が届かないため）。"
        ),
        epilog="詳しい手順は docs/usage.md を参照してください。",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="コマンド")

    # 必須の引数も argparse の required= にはしない（_REQUIRED の注を参照）。
    # 足りないときの文言を自前で組み立てるため、help に【必須】と書いて示す。
    sip = sub.add_parser("sip", help="入力フォルダ（または ZIP）から SIP を作る")
    sip.add_argument("--input", metavar="DIR", help="【必須】受け入れる資料のフォルダ、または ZIP ファイル")
    sip.add_argument("--output", metavar="DIR", help="【必須】SIP を書き出す先のフォルダ")
    sip.add_argument("--identifier", metavar="ID", help="【必須】移管の識別子（例: 2026-移管-総務課）")
    sip.add_argument("--title", metavar="TITLE", help="【必須】タイトル")
    sip.add_argument("--scope-note", default="", metavar="TEXT", help="内容・範囲（任意）")
    sip.add_argument("--date-note", default="", metavar="TEXT", help="年代（例: 2024–2025、任意）")
    sip.add_argument("--bag", action="store_true", help="BagIt bag として梱包する")
    sip.add_argument("--scan-pii", action="store_true", help="個人情報の候補を走査する")
    sip.add_argument("--virus-scan", action="store_true", help="ウイルス検査を行う（定義 DB が要る）")
    sip.add_argument(
        "--sanitize-filenames", action="store_true",
        help="ファイル名を安全な形に直す（元の名前は accession.csv に残す）",
    )
    sip.add_argument("--zip", action="store_true", help="できた SIP を無圧縮 ZIP に固める")
    sip.add_argument(
        "--prior-accession", metavar="CSV",
        help="前回の accession.csv。指定すると配列前後の対応表を出す",
    )
    _add_common(sip)

    aip = sub.add_parser("aip", help="SIP から AIP（BagIt bag + METS/PREMIS）を作る")
    aip.add_argument("--sip", metavar="DIR", help="【必須】入力の SIP（または bag）フォルダ")
    aip.add_argument("--output", metavar="DIR", help="【必須】AIP を書き出す先のフォルダ")
    aip.add_argument(
        "--no-normalize", action="store_true",
        help="保存用フォーマットへの変換を行わない（既定は行う）",
    )
    aip.add_argument("--archivist", default="", metavar="NAME", help="担当者名（PREMIS に残す）")
    aip.add_argument("--zip", action="store_true", help="できた AIP を無圧縮 ZIP に固める")
    _add_common(aip)

    inspect = sub.add_parser("inspect", help="できている SIP / AIP の中身を表示する")
    inspect.add_argument("package", metavar="PACKAGE_DIR", nargs="?", help="【必須】SIP / AIP のフォルダ")
    _add_common(inspect, quiet=False)

    check = sub.add_parser("check", help="同梱ツールとウイルス定義 DB、変換規則表の状態を見る")
    _add_common(check, quiet=False)

    return parser


def _add_common(parser: argparse.ArgumentParser, *, quiet: bool = True) -> None:
    parser.add_argument("--json", action="store_true", help="結果を JSON で標準出力に出す")
    if quiet:
        parser.add_argument("--quiet", action="store_true", help="進捗（標準エラー）を出さない")


#: 足りないと始められない引数。**argparse の required= は使わない。**
#: 文言を自前で組み立てて、足りないものと直し方を同じ場所に出すため。
_REQUIRED: dict[str, tuple[tuple[str, str], ...]] = {
    "sip": (("input", "--input"), ("output", "--output"),
            ("identifier", "--identifier"), ("title", "--title")),
    "aip": (("sip", "--sip"), ("output", "--output")),
    "inspect": (("package", "PACKAGE_DIR"),),
}

_EXAMPLES: dict[str, str] = {
    "sip": (
        f"{PROG} sip --input ./受入 --output ./出力 "
        "--identifier 2026-移管-総務課 --title 総務課文書"
    ),
    "aip": f"{PROG} aip --sip ./出力/2026-移管-総務課 --output ./保存",
    "inspect": f"{PROG} inspect ./出力/2026-移管-総務課",
}


def _check_required(args: argparse.Namespace) -> None:
    missing = [
        flag for attr, flag in _REQUIRED.get(args.command, ()) if not getattr(args, attr, None)
    ]
    if not missing:
        return
    raise _UsageError(
        f"次の引数が足りません: {', '.join(missing)}\n"
        f"  例: {_EXAMPLES[args.command]}\n"
        f"  使い方を見るには: {PROG} {args.command} --help"
    )


# --------------------------------------------------------------------------
# パスの確認
# --------------------------------------------------------------------------


def _input_root(value: str) -> Path:
    """SIP の入力。フォルダか ZIP でなければ、始める前に止める。"""
    path = Path(value).expanduser()
    if path.is_dir():
        return path
    if path.exists() and zip_io.is_zip(path):
        return path
    if path.exists():
        raise _UsageError(
            f"入力がフォルダでも ZIP でもありません: {path}\n"
            "  受け入れる資料の入ったフォルダか、それを固めた ZIP を指定してください。"
        )
    raise _UsageError(
        f"入力が見つかりません: {path}\n"
        "  パスの綴りと、外付けディスクやネットワークドライブが繋がっているかを確認してください。"
    )


def _existing_directory(value: str, *, label: str, hint: str) -> Path:
    path = Path(value).expanduser()
    if path.is_dir():
        return path
    raise _UsageError(f"{label}が見つかりません: {path}\n  {hint}")


def _existing_file(value: str, *, label: str, hint: str) -> Path:
    path = Path(value).expanduser()
    if path.is_file():
        return path
    raise _UsageError(f"{label}が見つかりません: {path}\n  {hint}")


def _output_directory(value: str, reporter: _Reporter) -> Path:
    """書き出し先。無ければ作るが、**親まで作ることはしない**。

    親ごと作ると、綴りを間違えたパスにも黙って書けてしまい、
    「作ったはずの SIP がどこにも無い」になる。1 段だけ作る。
    """
    path = Path(value).expanduser()
    if path.is_dir():
        return path
    if path.exists():
        raise _UsageError(f"出力先がフォルダではありません: {path}")
    if not path.parent.is_dir():
        raise _UsageError(
            f"出力先の親フォルダがありません: {path.parent}\n"
            "  先に親フォルダを作るか、既にあるフォルダを指定してください。"
        )
    path.mkdir()
    reporter.progress(f"出力先を作成しました: {path}")
    return path


# --------------------------------------------------------------------------
# sip
# --------------------------------------------------------------------------


def _cmd_sip(args: argparse.Namespace, reporter: _Reporter) -> int:
    input_path = _input_root(args.input)
    output_parent = _output_directory(args.output, reporter)
    prior = (
        _existing_file(
            args.prior_accession,
            label="前回の accession.csv",
            hint="前回作った SIP の metadata/submissionDocumentation/accession.csv を指定します。",
        )
        if args.prior_accession
        else None
    )

    options = SIPOptions(
        make_bag=args.bag,
        prior_accession_path=prior,
        scan_pii=args.scan_pii,
        scan_virus=args.virus_scan,
        sanitize_filenames=args.sanitize_filenames,
        serialize_zip=args.zip,
    )
    metadata = SIPMetadata(
        identifier=args.identifier,
        title=args.title,
        scope_note=args.scope_note,
        date_note=args.date_note,
    )

    try:
        result = sip_pipeline.run(
            input_path=input_path,
            output_parent=output_parent,
            metadata=metadata,
            options=options,
            progress=reporter.progress,
        )
    except SIPPipelineError as exc:
        raise _RunError(_with_next_step(exc.message)) from exc
    except OSError as exc:
        raise _RunError(f"ファイル操作に失敗しました: {exc}") from exc

    findings = _split_warnings(result.warnings)
    _report_findings(findings, reporter)

    outputs = {
        "description_csv": _as_str(result.spreadsheet_path),
        "report": _as_str(result.report_path),
        "pii_report": _as_str(result.pii_report_path),
        "arrangement_map": _as_str(result.arrangement_map_path),
        "zip": _as_str(result.zip_path),
    }
    payload = {
        "command": "sip",
        "status": "ok",
        "sip_path": str(result.sip_path),
        "file_count": result.file_count,
        "total_bytes": result.total_bytes,
        "bagged": result.bagged,
        "outputs": outputs,
        "findings": findings,
        "warnings": list(result.warnings),
    }

    lines = [
        f"SIP を作成しました: {result.sip_path}",
        f"  形式: {'BagIt bag' if result.bagged else 'SIP ディレクトリ'}",
        f"  ファイル: {result.file_count} 件"
        f"（{package_report.human_bytes(result.total_bytes)}）",
    ]
    lines += [f"  {label}: {value}" for label, value in _output_labels(outputs)]
    lines += _warning_lines(result.warnings)
    reporter.emit(payload, lines)
    return EXIT_OK


#: 成果物の表示名。JSON の鍵とは別に持つ（鍵は機械が読むので変えられない）。
_OUTPUT_LABELS = (
    ("description_csv", "記述シート"),
    ("report", "レポート"),
    ("pii_report", "個人情報レポート"),
    ("arrangement_map", "配列対応表"),
    ("zip", "ZIP"),
)


def _output_labels(outputs: dict[str, str | None]) -> list[tuple[str, str]]:
    return [(label, outputs[key]) for key, label in _OUTPUT_LABELS if outputs.get(key)]


# --------------------------------------------------------------------------
# aip
# --------------------------------------------------------------------------


def _cmd_aip(args: argparse.Namespace, reporter: _Reporter) -> int:
    sip_root = _existing_directory(
        args.sip,
        label="入力の SIP",
        hint="SIP コマンドが作ったフォルダ（objects/ と metadata/ を含むもの）を指定します。",
    )
    output_parent = _output_directory(args.output, reporter)

    options = AIPOptions(
        normalize=not args.no_normalize,
        serialize_zip=args.zip,
        archivist_name=args.archivist,
    )

    try:
        result = aip_pipeline.run(
            sip_root=sip_root,
            output_parent=output_parent,
            options=options,
            progress=reporter.progress,
        )
    except AIPPipelineError as exc:
        raise _RunError(_with_next_step(exc.message)) from exc
    except OSError as exc:
        raise _RunError(f"ファイル操作に失敗しました: {exc}") from exc

    fixity = {
        "outcome": result.fixity.outcome.value,
        "checked": result.fixity.checked,
        "mismatches": list(result.fixity.mismatches),
        "reason": result.fixity.reason,
    }
    # 照合が通らなかったのは「入力の SIP が受入時と変わっている」ということ。
    # AIP 自体は作れている（PREMIS にも記録した）ので失敗にはしないが、
    # 黙って成功と同じ顔をされては困るので必ず知らせる。
    if fixity["outcome"] == "failed":
        reporter.notice(
            f"【確認が必要】入力 SIP の完全性の確認に失敗しました"
            f"（不一致 {len(result.fixity.mismatches)} 件）。"
            "受入時から中身が変わっています。AIP は作成し、PREMIS に記録しました。"
        )
        for path in result.fixity.mismatches[:10]:
            reporter.notice(f"  - {path}")

    payload = {
        "command": "aip",
        "status": "ok",
        "aip_path": str(result.aip_path),
        "aip_uuid": result.aip_uuid,
        "mets_path": str(result.mets_path),
        "original_count": result.original_count,
        "derivative_count": result.derivative_count,
        "fixity": fixity,
        "outputs": {"zip": _as_str(result.zip_path)},
        "warnings": list(result.warnings),
    }

    lines = [
        f"AIP を作成しました: {result.aip_path}",
        f"  AIP UUID: {result.aip_uuid}",
        f"  METS: {result.mets_path}",
        f"  原本: {result.original_count} 件 / 保存用に変換: {result.derivative_count} 件",
        f"  完全性の確認: {_fixity_label(result.fixity)}",
    ]
    if result.zip_path:
        lines.append(f"  ZIP: {result.zip_path}")
    lines += _warning_lines(result.warnings)
    reporter.emit(payload, lines)
    return EXIT_OK


def _fixity_label(status) -> str:
    if status.outcome.value == "passed":
        return f"一致（{status.checked} 件）"
    if status.outcome.value == "failed":
        return f"不一致 {len(status.mismatches)} 件"
    return f"実施せず（{status.reason or '照合できるマニフェストがありません'}）"


# --------------------------------------------------------------------------
# inspect
# --------------------------------------------------------------------------


def _cmd_inspect(args: argparse.Namespace, reporter: _Reporter) -> int:
    root = _existing_directory(
        args.package,
        label="パッケージ",
        hint="SIP か AIP のフォルダ（objects/ や data/ を含むもの）を指定します。",
    )
    try:
        report = package_report.read(root)
    except OSError as exc:
        raise _RunError(f"パッケージを読めませんでした: {exc}") from exc

    overview = report.overview
    if not report.files and overview.note:
        raise _RunError(
            f"SIP / AIP として読めませんでした: {overview.note}\n"
            "  このアプリが作ったパッケージのフォルダを指定してください"
            "（bag のときは bagit.txt のある階層）。"
        )

    payload = {
        "command": "inspect",
        "status": "ok",
        "root": str(root),
        "overview": {
            "kind": overview.kind,
            "title": overview.title,
            "identifier": overview.identifier,
            "created": overview.created,
            "file_count": overview.file_count,
            "original_count": overview.original_count,
            "total_bytes": overview.total_bytes,
            "note": overview.note,
        },
        "summary": {
            "formats": [{"name": n, "count": c} for n, c in report.summary.formats],
            "events": [{"type": n, "count": c} for n, c in report.summary.events],
            "normalized": report.summary.normalized,
            "unidentified": report.summary.unidentified,
            "extension_warnings": report.summary.extension_warnings,
            "virus_scanned": report.summary.virus_scanned,
        },
        "events": [
            {
                "date_time": e.date_time, "type": e.type_label, "outcome": e.outcome,
                "detail": e.detail, "agent": e.agent, "target": e.target,
            }
            for e in report.events
        ],
        # 機械が読むほうは全件出す。人が読むほうは先頭だけにする（下）。
        "files": [
            {
                "path": f.path, "use": f.use, "format_name": f.format_name, "puid": f.puid,
                "size": f.size, "sha256": f.sha256, "virus": f.virus, "warning": f.warning,
            }
            for f in report.files
        ],
        "mets_path": _as_str(report.mets_path),
    }

    lines = [f"{overview.kind or 'パッケージ'}: {root}"]
    if overview.title:
        lines.append(f"  タイトル: {overview.title}")
    if overview.identifier:
        lines.append(f"  識別子: {overview.identifier}")
    if overview.created:
        lines.append(f"  作成: {overview.created}")
    lines.append(
        f"  ファイル: {overview.file_count} 件（うち原本 {overview.original_count} 件 / "
        f"{package_report.human_bytes(overview.total_bytes)}）"
    )
    if report.mets_path:
        lines.append(f"  METS: {report.mets_path}")
    if overview.note:
        lines.append(f"  注: {overview.note}")

    if report.summary.formats:
        lines.append("フォーマット（多い順）")
        lines += [f"  {count:>5}  {name}" for name, count in report.summary.formats]
    lines.append(
        f"確認したい点: 未識別 {report.summary.unidentified} 件 / "
        f"拡張子不一致 {report.summary.extension_warnings} 件 / "
        f"ウイルス検査済み {report.summary.virus_scanned} 件 / "
        f"保存用に変換 {report.summary.normalized} 件"
    )

    if report.events:
        lines.append("処理の記録（PREMIS）")
        lines += [
            f"  {e.date_time}  {e.type_label}  {e.outcome}"
            + (f"  {e.target}" if e.target else "")
            for e in report.events[:INSPECT_FILE_PREVIEW]
        ]
        if len(report.events) > INSPECT_FILE_PREVIEW:
            lines.append(f"  …ほか {len(report.events) - INSPECT_FILE_PREVIEW} 件（--json で全件）")

    lines.append("ファイル")
    lines += [
        f"  {f.use or '原本':<6} {f.path}  {f.format_name or '（未識別）'}"
        for f in report.files[:INSPECT_FILE_PREVIEW]
    ]
    if len(report.files) > INSPECT_FILE_PREVIEW:
        lines.append(f"  …ほか {len(report.files) - INSPECT_FILE_PREVIEW} 件（--json で全件）")

    reporter.emit(payload, lines)
    return EXIT_OK


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace, reporter: _Reporter) -> int:
    """使える道具と定義 DB の状態を見せる。**判定はしない。**

    ここで終了コードを 1 にしたくなるが、しない。同梱ツールが無いのは
    「ソースから動かしている」だけで正常な状態であり（配布物には入っている）、
    失敗にすると CI の最初の 1 行が常に赤くなって誰も読まなくなる。
    足りないものは文言で伝え、判断は読んだ人に委ねる。
    """
    del args

    database = clamav.database_directory()
    tools = {
        "siegfried": _as_str(bundled.find("sf")),
        "siegfried_signature": _as_str(bundled.signature_home()),
        "clamscan": _as_str(clamav.find_tool()),
        "freshclam": _as_str(clamav.find_updater()),
    }
    table = conversion_registry.table()
    user_rules = rule_table.user_table_path()

    payload = {
        "command": "check",
        "status": "ok",
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "bundled_tools": tools,
        "virus_database": {
            "directory": str(database),
            "present": clamav.has_database(database),
            "status": clamav.database_status(database),
        },
        "normalization_rules": {
            "user_table": str(user_rules),
            "user_table_present": user_rules.is_file(),
            "rule_count": len(table.rules),
            "warnings": list(table.warnings),
        },
    }

    lines = [
        f"{PROG} {__version__}（Python {platform.python_version()} / {platform.platform()}）",
        "同梱ツール",
        f"  フォーマット識別 siegfried: {tools['siegfried'] or '無し（PUID が空になります）'}",
        f"  シグネチャ default.sig:     {tools['siegfried_signature'] or '無し'}",
        f"  ウイルス検査 clamscan:      {tools['clamscan'] or '無し（検査はスキップされます）'}",
        f"  定義取得 freshclam:         {tools['freshclam'] or '無し'}",
        clamav.database_status(database),
        f"  置き場所: {database}",
        "変換規則表",
        f"  引き当てられる PUID: {len(table.rules)} 件",
        f"  利用者の表: {user_rules}"
        f"（{'あり' if user_rules.is_file() else '未作成。無くても動きます'}）",
    ]
    lines += [f"  警告: {w}" for w in table.warnings]
    if not tools["clamscan"]:
        lines.append(
            "ウイルス検査を使うには、同梱ツールのあるビルドで動かすか、"
            "binaries/ にツールを置いてください（README の「外部ツールの同梱方針」）。"
        )
    elif not clamav.has_database(database):
        lines.append("ウイルス定義は別途取得が必要です（画面の「定義を取得 / 更新」から）。")

    reporter.emit(payload, lines)
    return EXIT_OK


# --------------------------------------------------------------------------
# 共通の後始末
# --------------------------------------------------------------------------


#: 警告の種類。sip_builder.collect_warnings が作る前置きで分ける。
#:
#: **SIPResult は件数を持たない。** 手元にあるのは警告の文字列だけで、
#: それを分けるには前置きで見るしかない（core は読むだけと決めている）。
#: 前置きが変わればここも変わるので、テストで結びつけてある。
_FINDING_PREFIXES = (
    ("virus", "ウイルス検出: "),
    ("pii", "PII候補: "),
    ("unidentified", "未識別: "),
    ("extension_mismatch", "拡張子不一致: "),
)


def _split_warnings(warnings: Sequence[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {key: [] for key, _ in _FINDING_PREFIXES}
    for warning in warnings:
        for key, prefix in _FINDING_PREFIXES:
            if warning.startswith(prefix):
                found[key].append(warning[len(prefix):])
                break
    return found


def _report_findings(findings: dict[str, list[str]], reporter: _Reporter) -> None:
    """検出は失敗にしないが、**必ず見えるところに出す**。

    終了コードが 0 なので、標準エラーに出さないと自動処理では誰も気づかない。
    「検査したが何も無かった」と「検出したが処理は通した」を取り違えさせない。
    """
    for key, label in (("virus", "ウイルス"), ("pii", "個人情報の候補")):
        hits = findings.get(key) or []
        if not hits:
            continue
        reporter.notice(
            f"【確認が必要】{label}を {len(hits)} 件検出しました。"
            "パッケージは作成しています（取り扱いは人が判断してください）。"
        )
        for hit in hits[:10]:
            reporter.notice(f"  - {hit}")
        if len(hits) > 10:
            reporter.notice(f"  …ほか {len(hits) - 10} 件")


def _warning_lines(warnings: Sequence[str]) -> list[str]:
    if not warnings:
        return []
    lines = [f"  警告: {len(warnings)} 件"]
    lines += [f"    - {w}" for w in warnings[:10]]
    if len(warnings) > 10:
        lines.append(f"    …ほか {len(warnings) - 10} 件（--json で全件）")
    return lines


#: 失敗の文言に足す「次にやること」。core の例外は原因までしか言わない。
_NEXT_STEPS = (
    ("対象ファイルがありません",
     "入力フォルダに資料が入っているか確認してください（隠しファイルは数えません）。"),
    ("SIP として認識できません", "SIP コマンドが作ったフォルダを指定してください（objects/ を含む階層）。"),
    ("objects/ に対象ファイルがありません", "入力の SIP の objects/ が空でないか確認してください。"),
    ("必要なツールが見つかりません", "check コマンドで同梱ツールの状態を確認してください。"),
    ("変換ツールが見つかりません", "--no-normalize を付けると変換せずに AIP を作れます。"),
)


def _with_next_step(message: str) -> str:
    for marker, step in _NEXT_STEPS:
        if marker in message:
            return f"{message}\n  {step}"
    return message


def _as_str(path: Path | None) -> str | None:
    return str(path) if path is not None else None


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


_COMMANDS = {"sip": _cmd_sip, "aip": _cmd_aip, "inspect": _cmd_inspect, "check": _cmd_check}


def main(argv: Sequence[str] | None = None) -> int:
    # Windows の標準出力は既定が cp932 で、日本語のパスを書くだけで落ちることがある。
    # 落ちると「作れたのに失敗に見える」ので、出せる形に直しておく。
    # 差し替えられている場合（pytest の捕捉）は触らない。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse は --help / --version / 引数の誤りで SystemExit を投げる。
        # **main() は終了コードを返す関数**として使えるようにしておく
        # （テストから呼ぶため、また他のスクリプトから import して使えるように）。
        return int(exc.code or EXIT_OK)

    if not args.command:
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    reporter = _Reporter(as_json=args.json, quiet=getattr(args, "quiet", False))
    try:
        _check_required(args)
        return _COMMANDS[args.command](args, reporter)
    except _UsageError as exc:
        reporter.emit_error(args.command, str(exc))
        return EXIT_USAGE
    except _RunError as exc:
        reporter.emit_error(args.command, str(exc))
        return EXIT_FAILURE
    except KeyboardInterrupt:
        reporter.notice("中断しました。途中まで書き出したフォルダが残っている場合は手で消してください。")
        return EXIT_FAILURE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
