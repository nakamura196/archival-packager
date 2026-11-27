"""Archivematica の FPR 書式（`manage.py loaddata` の JSON）で書いた変換規則。

## なぜ Archivematica の書式にするのか

AP の規則は、これまで AP 独自の TOML だった。これだと、同じ変換を Archivematica にも
入れたい人は規則を 2 回書くことになり、変換のスクリプトも別々に育ってしまう。
Archivematica は規則を「FPR（Format Policy Registry）」という表に持ち、その表を
JSON で出し入れできる（loaddata の fixture）。**その JSON をそのまま読めば、
1 つのファイルと 1 つのスクリプトが両方で動く。**

読むのは次の 4 種類のレコードだけ。

    fpr.fptool       道具（LibreOffice など）。画面と PREMIS の名前に使う
    fpr.fpcommand    変換のスクリプト・検証・eventDetail 用の 3 種
    fpr.fprule       「この形式に、この命令を使う」
    fpr.formatversion  形式（利用者が足したいときだけ。普通は同梱の対応表で足りる）

## 形式は UUID で書かれている

FPR の規則は PRONOM の PUID ではなく、Archivematica 内の形式の UUID で形式を指す。
AP は siegfried が返す PUID で規則を引くので、UUID から PUID への対応が要る。
同梱の対応表（fpr_formats.py。Archivematica の初期データから識別子だけを写した物）で
引き、fixture 自身に fpr.formatversion があればそちらを優先する。

## 実行は Archivematica の動きに合わせる

- `bashScript` は、その全文を bash で実行する。`command` は 1 行のコマンドで、
  Archivematica と同じくシェルを通さずに分割して実行する。
- `%fileFullName%` のような印は、実行の前に値へ置き換える（Archivematica と同じ印と同じ値）。
  **ただし 1 回の走査で置き換える。** Archivematica は印ごとに順に置き換えるので、
  ファイル名に別の印の文字列（"%prefix%.docx" など）が入っていると、その部分まで
  置き換わってしまう。AP は資料のファイル名を選べないので、この誤動作は避ける。
- 変換のあと、検証の命令が 0 以外で終われば変換の失敗。eventDetail の命令の標準出力は、
  PREMIS の eventDetail にそのまま入る（Archivematica の normalize.py と同じ）。

## 実行できない物は、読み込みの時点で理由を付けて外す

pythonScript・as_is は扱わない。任意の Python を実行時に読み込む道は、AP が避けている
（aip_models.Executor の注を参照）。bash の無い Windows の bashScript も外す。
動かない規則を黙って読み込むと、AIP を作ったあとの警告で初めて分かることになる。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import uuid as _uuid
from collections.abc import Mapping
from pathlib import Path

from . import bundled
from .aip_models import DerivativePurpose, FprCommand, FprSpec, NormalizationRule, RuleSource
from .fpr_formats import FORMATS

#: 実行できる script_type。
SUPPORTED_SCRIPT_TYPES = ("bashScript", "command")

#: 規則にできる purpose。Archivematica には ほかに default_access・thumbnail・
#: characterization・validation・extract などがあるが、AP の AIP に対応する物が無い。
_PURPOSES = {
    "preservation": DerivativePurpose.PRESERVATION,
    "access": DerivativePurpose.ACCESS,
}

#: 使える印。Archivematica の ReplacementDict のうち、1 ファイルの変換で意味を持つもの。
#: %fileUUID% などデータベースに依る物は持たない（%fileUUID% は AIPFile.uuid で足す）。
PLACEHOLDERS = (
    "%fileFullName%", "%inputFile%", "%currentLocation%", "%relativeLocation%",
    "%fileName%", "%fileExtension%", "%fileExtensionWithDot%", "%fileDirectory%",
    "%fileUUID%",
    "%outputDirectory%", "%prefix%", "%postfix%",
    "%outputFileName%", "%outputFilePath%", "%outputLocation%",
)

_TOKEN_RE = re.compile(r"%[A-Za-z]+%")
_EXTENSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: eventDetail の命令が出す `program="…"` と `version="…"`。
_PROGRAM_RE = re.compile(r'program="([^"]*)"')
_VERSION_RE = re.compile(r'version="([^"]*)"')


class FprError(Exception):
    """FPR の命令を走らせられなかった（呼び出し側で変換の失敗として扱う）。"""


# --------------------------------------------------------------------------
# 読み込み
# --------------------------------------------------------------------------


def bash_available() -> bool:
    """bashScript を走らせられる環境か。

    Windows では PATH 上の bash が WSL の入口であることがあり、資料のパスの
    渡り方が変わる。**動くかどうか分からない物を黙って使わず**、外す。
    """
    return os.name != "nt" and _find_bash() is not None


def _find_bash() -> str | None:
    return shutil.which("bash", path=tool_path_env())


def tool_path_env() -> str:
    """PATH に、利用者がソフトを入れる定番の場所を後ろから足した物。

    画面から開いたアプリの PATH は短く、Homebrew の soffice などが見えない
    （normalizer.extra_tool_dirs の注を参照）。ここも同じ解決にして、
    スクリプトの中で呼ぶコマンドが、ターミナルから開いたときと同じように見つかるようにする。
    """
    from .normalizer import extra_tool_dirs

    base = os.environ.get("PATH", os.defpath)
    extra = [str(d) for d in extra_tool_dirs() if str(d) not in base.split(os.pathsep)]
    return os.pathsep.join([base, *extra]) if extra else base


def _records(data: list, model: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for rec in data:
        if isinstance(rec, dict) and rec.get("model") == model and isinstance(rec.get("fields"), dict):
            fields = rec["fields"]
            uid = fields.get("uuid")
            if isinstance(uid, str) and uid:
                found[uid] = fields
    return found


def _command_problem(label: str, command: FprCommand) -> str:
    """その命令を、この環境で走らせられるか。走らせられなければ理由。"""
    if command.script_type not in SUPPORTED_SCRIPT_TYPES:
        return (
            f"{label}の script_type \"{command.script_type}\" は扱えません"
            f"（扱えるのは {' / '.join(SUPPORTED_SCRIPT_TYPES)}）。"
        )
    if command.script_type == "bashScript" and not bash_available():
        return f"{label}は bashScript ですが、この環境には bash が無いため実行できません。"
    return ""


def _unknown_tokens(command: FprCommand) -> list[str]:
    return sorted({t for t in _TOKEN_RE.findall(command.text) + _TOKEN_RE.findall(command.output_location)
                   if t not in PLACEHOLDERS})


def _to_command(fields: dict, *, with_output: bool) -> FprCommand:
    return FprCommand(
        uuid=str(fields.get("uuid", "")),
        script_type=str(fields.get("script_type") or ""),
        text=str(fields.get("command") or ""),
        output_location=str(fields.get("output_location") or "") if with_output else "",
    )


def parse(text: str) -> tuple[list[NormalizationRule], list[str]]:
    """FPR の JSON を規則の並びにする。壊れていても例外にしない。

    rule_table.parse と同じ考え方で、**表が壊れていてもアプリは起動する**。
    読めなかった規則は黙って捨てず、理由（日本語）を持ち帰る。
    返す規則の source は常に USER（組み込みの規則は FPR の形では持たない）。
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"rules.json の書式が壊れています（{exc}）。このファイルは使いません。"]
    if not isinstance(data, list):
        return [], ["rules.json は、レコード（model と fields を持つ物）の配列で書いてください。"
                    "この表は使いません。"]

    tools = _records(data, "fpr.fptool")
    commands = _records(data, "fpr.fpcommand")
    own_formats = _records(data, "fpr.formatversion")
    fprules = [
        r["fields"] for r in data
        if isinstance(r, dict) and r.get("model") == "fpr.fprule" and isinstance(r.get("fields"), dict)
    ]

    def puid_of(format_uuid: object) -> str | None:
        if not isinstance(format_uuid, str):
            return None
        own = own_formats.get(format_uuid)
        if own is not None:
            return own.get("pronom_id") or None
        return FORMATS.get(format_uuid)

    rules: list[NormalizationRule] = []
    warnings: list[str] = []
    for index, fields in enumerate(fprules):
        rule_id = str(fields.get("uuid") or "")
        where = f"規則 {rule_id}" if rule_id else f"{index + 1} 件目の fpr.fprule"
        if not rule_id:
            warnings.append(f"{where}: uuid がありません。")
            continue
        if fields.get("enabled") is False:
            continue  # 無効にしてある規則は、読み込まないのが正しい動き
        purpose = _PURPOSES.get(str(fields.get("purpose") or ""))
        if purpose is None:
            # 対応する出口が AP に無い purpose（thumbnail・characterization など）。
            # Archivematica 向けの規則を同じファイルに置いてあるだけなので、咎めない。
            continue

        rule, problems = _build_rule(
            rule_id, fields, purpose, commands, tools, own_formats, puid_of
        )
        warnings += [f"{where}: {p}" for p in problems if p]
        if rule is not None:
            rules.append(rule)
    return rules, warnings


def _build_rule(
    rule_id: str,
    fields: dict,
    purpose: DerivativePurpose,
    commands: dict[str, dict],
    tools: dict[str, dict],
    own_formats: dict[str, dict],
    puid_of,
) -> tuple[NormalizationRule | None, list[str]]:
    """fpr.fprule 1 件を NormalizationRule にする。作れなければ理由を返す。"""
    puid = puid_of(fields.get("format"))
    if not puid:
        return None, [
            f"形式 {fields.get('format')} を PUID に直せません。同梱の対応表に無い形式です"
            "（rules.json に fpr.formatversion を足し、pronom_id を書けば使えます）。"
        ]

    cmd_fields = commands.get(str(fields.get("command")))
    if cmd_fields is None:
        return None, [f"command {fields.get('command')} の fpr.fpcommand が同じファイルにありません。"]
    if cmd_fields.get("enabled") is False:
        # 規則だけ有効にして命令を有効にし忘れる、が最も起きやすい。黙って外すと理由が分からない。
        return None, ["command の fpr.fpcommand が \"enabled\": false です。命令も true にしてください。"]
    usage = cmd_fields.get("command_usage")
    if usage != "normalization":
        return None, [
            f"command の command_usage が \"{usage}\" です（\"normalization\" の命令を指してください）。"
        ]

    command = _to_command(cmd_fields, with_output=True)
    problems: list[str] = []
    problem = _command_problem("変換の命令", command)
    if problem:
        return None, [problem]

    if not command.output_location.startswith("%outputDirectory%"):
        # 変換結果を AP が拾って移す。出力先を作業用のフォルダの外に向けられると、
        # 無関係なファイルを派生物として取り込んでしまう。
        return None, ["output_location は %outputDirectory% で始めてください。"
                      "例: %outputDirectory%%prefix%%fileName%%postfix%.pdf"]
    ext = Path(command.output_location).suffix.lstrip(".")
    if not _EXTENSION_RE.match(ext):
        return None, [f"output_location の末尾に拡張子がありません（指定: \"{command.output_location}\"）。"]

    follow: dict[str, FprCommand | None] = {}
    for key, label, usage_name in (
        ("verification_command", "検証の命令", "verification"),
        ("event_detail_command", "eventDetail の命令", "event_detail"),
    ):
        ref = cmd_fields.get(key)
        if not ref:
            follow[key] = None
            continue
        ref_fields = commands.get(str(ref))
        if ref_fields is None:
            return None, [f"{key} {ref} の fpr.fpcommand が同じファイルにありません。"]
        if ref_fields.get("command_usage") != usage_name:
            problems.append(f"{label}の command_usage が \"{ref_fields.get('command_usage')}\" です。")
        sub = _to_command(ref_fields, with_output=False)
        sub_problem = _command_problem(label, sub)
        if sub_problem:
            # 検証が走らないまま変換だけ通すと、利用者が期待した確認が黙って消える。
            return None, [sub_problem]
        follow[key] = sub

    for label, cmd in (("変換の命令", command), ("検証の命令", follow["verification_command"]),
                       ("eventDetail の命令", follow["event_detail_command"])):
        if cmd is None:
            continue
        unknown = _unknown_tokens(cmd)
        if unknown:
            # 外さない。シェルの printf の書式など、印に見えるだけの文字列があり得るため。
            # 本当の打ち間違いなら、変換が出力を作れず失敗として現れる。
            problems.append(f"{label}に、使えない印に見える文字列があります: {', '.join(unknown)}"
                            f"（使える印は docs/rules.md を参照）。")

    tool_fields = tools.get(str(cmd_fields.get("tool")), {})
    tool_name = str(tool_fields.get("description") or "") or "fpr"

    out_fields = own_formats.get(str(cmd_fields.get("output_format")))
    puid_out = puid_of(cmd_fields.get("output_format"))
    name_out = str(out_fields.get("description") or "") or None if out_fields else None

    return (
        NormalizationRule(
            puid_in=puid,
            purpose=purpose,
            tool=tool_name,
            args=[],
            out_extension=ext,
            rule_id=rule_id,
            puid_out=puid_out,
            format_name_out=name_out,
            executor=_executor(),
            source=RuleSource.USER,
            fpr=FprSpec(
                command=command,
                description=str(cmd_fields.get("description") or ""),
                verification=follow["verification_command"],
                event_detail=follow["event_detail_command"],
            ),
        ),
        problems,
    )


def _executor():
    from .aip_models import Executor

    return Executor.FPR


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------


def replacements(
    input_path: Path, *, output_dir: Path, postfix: str, file_uuid: str = ""
) -> dict[str, str]:
    """Archivematica の normalize.py と同じ値で、印の表を作る。

    保存用（preservation）の値に合わせる: prefix は空、postfix は "-<一意な文字列>"、
    出力先は変換用の作業フォルダ（末尾に区切りを付ける）。
    **postfix に一意な文字列を入れる**のは、同じ名前の原本が別の場所にあっても
    出力が衝突しないようにするため（Archivematica は task の uuid を入れる）。
    """
    stem, ext = os.path.splitext(input_path.name)
    out_dir = str(output_dir) + os.sep
    full = str(input_path)
    out_name = f"{stem}{postfix}"
    return {
        "%fileFullName%": full,
        "%inputFile%": full,
        "%currentLocation%": full,
        "%relativeLocation%": full,
        "%fileName%": stem,
        "%fileExtension%": ext[1:],
        "%fileExtensionWithDot%": ext,
        "%fileDirectory%": str(input_path.parent),
        "%fileUUID%": file_uuid,
        "%outputDirectory%": out_dir,
        "%prefix%": "",
        "%postfix%": postfix,
        "%outputFileName%": out_name,
        "%outputFilePath%": os.path.join(out_dir, out_name),
    }


def substitute(text: str, values: Mapping[str, str]) -> str:
    """印を値に置き換える。**1 回の走査で**（理由はこのファイルの冒頭）。知らない印はそのまま残す。"""
    return _TOKEN_RE.sub(lambda m: values.get(m.group(0), m.group(0)), text)


def run_command(command: FprCommand, values: Mapping[str, str]) -> tuple[int, str, str, str]:
    """FPR の命令 1 つを走らせる。(終了コード, 標準出力, 標準エラー, 実行した文面) を返す。

    OSError（コマンドを起こせない）は FprError にする。
    """
    text = substitute(command.text, values)
    env = dict(os.environ)
    env["PATH"] = tool_path_env()

    if command.script_type == "bashScript":
        bash = _find_bash()
        if os.name == "nt" or bash is None:
            raise FprError("bash が見つかりません")
        argv = [bash, "-c", text]
    elif command.script_type == "command":
        # Archivematica と同じく、シェルを通さずに分割する（パイプや && は効かない）。
        try:
            argv = shlex.split(text)
        except ValueError as exc:
            raise FprError(f"コマンドを分割できません: {exc}") from exc
        if not argv:
            raise FprError("コマンドが空です")
    else:
        raise FprError(f"script_type \"{command.script_type}\" は扱えません")

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **bundled.no_window(),
        )
    except OSError as exc:
        raise FprError(str(exc)) from exc
    return proc.returncode, proc.stdout or "", proc.stderr or "", text


def parse_event_detail(stdout: str) -> tuple[str, str]:
    """eventDetail の命令の出力から (program, version) を取る。無ければ空文字。"""
    program = _PROGRAM_RE.search(stdout)
    version = _VERSION_RE.search(stdout)
    return (program.group(1) if program else "", version.group(1) if version else "")


def new_postfix() -> tuple[str, str]:
    """(派生物の uuid, postfix)。"""
    uid = str(_uuid.uuid4())
    return uid, f"-{uid}"


def inside(path: Path, directory: Path) -> bool:
    """path が directory の中にあるか（.. を解いてから見る）。"""
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError:
        return False
    return True
