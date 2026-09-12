"""正規化規則の表（Archivematica の FPR に相当するものを、データとして持つ）。

## なぜ表にするのか

これまで変換規則は Python に直書きしてあり、**利用者は 1 件も足せなかった**。
音声を FLAC にしたい、独自の変換を挟みたい、といった要望は、アプリを作り直す
以外に応えようがなかった。Archivematica は Format Policy Registry（FPR）という
表で同じ問題を解いている。ここではその極小版を、1 ファイルの TOML で持つ。

## コードは読み込まない

Tropy のようにプラグイン（実行可能なコード）を差し込む方式は**採らない**。
このアプリは Apple の公証と Microsoft ストアの審査を通して配布している。
実行時に外部のコードを読み込めば、その審査が見たものと実際に動くものが
別になる。資料を預かる道具なので、ここは保守的にする。

読み込むのは**データ（この表）だけ**。実行するのは、利用者の環境に既にある
外部コマンドだけ。

## 組み込みの規則も同じ表に載せる

組み込みの 2 規則（画像→TIFF、PostScript/EPS→PDF）も、利用者が書くのと
まったく同じ TOML で書いてある（BUILTIN_TOML）。**組み込みが別の道を通ると、
利用者向けの道は誰も使わないまま壊れる。** 同じ道を通していれば、アプリを
起動するたび・テストを回すたびに、読み込みと検証が実際に動く。

組み込みの表を「同梱する TOML ファイル」ではなく Python の文字列定数に
したのは、配布物の作り方（PyInstaller / MSIX）に手を入れずに済ませるため。
データファイルを 1 枚足すには packaging 側の設定が要り、入れ忘れると
**配布版だけ規則が空になる**という、手元では絶対に再現しない壊れ方をする。
文字列なら import できた時点で必ず在る。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .aip_models import DerivativePurpose, Executor, NormalizationRule, RuleSource

# --------------------------------------------------------------------------
# 組み込みの表
# --------------------------------------------------------------------------

#: 組み込み規則。**利用者が書く TOML と同じ書式**で持つ（この形を崩さないこと）。
#:
#: id は一度出したら変えない。過去に作った AIP の PREMIS にこの文字列が
#: 書き込まれており、改名すると突き合わせられなくなる。
BUILTIN_TOML = """\
# 組み込みの正規化規則。利用者が書く rules.toml とまったく同じ書式で書いてある。
# ここを変えるときは docs/rules.md も合わせて直すこと。

# 画像 → 非圧縮 TIFF。アプリ内（Pillow）で変換するので外部ツールを要しない。
# sips は Windows に無く、ImageMagick は両OSで別ビルドになるため使わない
# （同じ資料から作った派生物のバイト列が OS によって変わると説明できない）。
[[rule]]
id = "image-to-tiff"
purpose = "preservation"
executor = "builtin"
tool = "pillow"
args = []
puid_in = [
  "fmt/11", "fmt/12", "fmt/13", "fmt/935",            # PNG
  "fmt/41", "fmt/42", "fmt/43", "fmt/44",             # JPEG
  "x-fmt/398", "x-fmt/390", "x-fmt/391",              # JPEG (旧)
  "fmt/3", "fmt/4",                                   # GIF
  "fmt/116", "fmt/117", "fmt/119", "x-fmt/270",       # BMP
]
puid_out = "fmt/353"
format_name_out = "Tagged Image File Format"
out_extension = "tiff"

# PostScript / EPS → PDF。Ghostscript は AGPL-3.0 のため同梱していない。
# PATH 上に gs があれば使い、無ければ原本のまま保存して report に警告を出す。
[[rule]]
id = "postscript-to-pdf"
purpose = "preservation"
executor = "command"
tool = "gs"
args = ["-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite", "-sOutputFile={out}", "{in}"]
puid_in = [
  "fmt/124", "fmt/501",                               # PostScript
  "x-fmt/91", "x-fmt/406", "x-fmt/407", "x-fmt/408",  # PostScript (旧)
  "fmt/122", "fmt/123",                               # EPS
]
puid_out = "fmt/276"
format_name_out = "Acrobat PDF 1.7 - Portable Document Format"
out_extension = "pdf"
"""

#: 利用者の表のファイル名。記録（errors.log）と同じディレクトリに置く。
#: 場所を増やさないのは、「どこに置けばよいか」が利用者にとって最大の障壁だから。
USER_FILE_NAME = "rules.toml"

#: AIP に同梱するときの名前。
DOCUMENT_NAME = "normalization-rules.toml"


def user_table_path() -> Path:
    """利用者の規則表の置き場所。"""
    from . import applog

    return applog.log_path().parent / USER_FILE_NAME


# --------------------------------------------------------------------------
# 検証
# --------------------------------------------------------------------------

#: args の中で実パスに置き換えられる印。**これ以外は認めない。**
#: 未知の印を素通しすると、置換されないまま外部コマンドに渡り、
#: 「{tmp} という名前のファイルが無い」といった分かりにくい失敗になる。
PLACEHOLDERS = ("{in}", "{out}")

_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")

#: 出力拡張子に許す形。派生物のファイル名の一部になるので、
#: パス区切りや `..` が混じると別の場所に書きに行ってしまう。
_EXTENSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(slots=True)
class RuleEntry:
    """表の 1 行。TOML の [[rule]] 1 つに対応する。

    NormalizationRule は PUID 1 つにつき 1 件だが、表の 1 行は
    複数の PUID をまとめて受け持つ（同じ変換を PNG にも JPEG にも掛けるため）。
    """

    rule_id: str
    puid_in: list[str]
    purpose: DerivativePurpose
    executor: Executor
    tool: str
    args: list[str]
    out_extension: str
    source: RuleSource
    puid_out: str | None = None
    format_name_out: str | None = None

    def to_rules(self) -> list[NormalizationRule]:
        return [
            NormalizationRule(
                puid_in=puid,
                purpose=self.purpose,
                tool=self.tool,
                args=list(self.args),
                out_extension=self.out_extension,
                rule_id=self.rule_id,
                puid_out=self.puid_out,
                format_name_out=self.format_name_out,
                executor=self.executor,
                source=self.source,
            )
            for puid in self.puid_in
        ]


def _as_str(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


def _as_str_list(raw: object) -> list[str] | None:
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        return None
    return list(raw)


def validate(raw: object, *, source: RuleSource, index: int) -> tuple[RuleEntry | None, list[str]]:
    """[[rule]] 1 件を検証する。通らなければ理由（日本語）を返す。

    **落とした理由は利用者が読んで直せる言葉で書く。** 表を書くのは
    情報システムの専門家ではない。「invalid executor」では何をどう直せばよいか
    分からず、結局アプリを使うのをやめることになる。
    """
    where = f"{index + 1} 件目の規則"
    if not isinstance(raw, dict):
        return None, [f"{where}: [[rule]] の中身が表になっていません。"]

    rule_id = _as_str(raw.get("id"))
    if not rule_id:
        return None, [f"{where}: id がありません。規則を見分ける名前を id に書いてください。"]
    where = f"規則 {rule_id}"

    puid_in = _as_str_list(raw.get("puid_in"))
    if not puid_in:
        return None, [
            f"{where}: puid_in がありません。"
            'どのフォーマットを変換するかを puid_in = ["fmt/11"] の形で書いてください。'
        ]

    purpose_raw = _as_str(raw.get("purpose")) or DerivativePurpose.PRESERVATION.value
    if purpose_raw != DerivativePurpose.PRESERVATION.value:
        # access（利用用）の派生物はまだ作れない。受け付けてしまうと、
        # 保存用として記録された利用用ファイルが AIP に入る。
        # 記録が実態と違うほうが、機能が無いことより悪い。
        return None, [
            f"{where}: purpose は今のところ \"preservation\" のみ対応しています"
            f"（指定: \"{purpose_raw}\"）。"
        ]
    purpose = DerivativePurpose.PRESERVATION

    executor_raw = _as_str(raw.get("executor")) or ""
    if executor_raw == Executor.BUILTIN.value:
        if source is RuleSource.USER:
            # **内蔵処理を外から呼ばせない。** 呼べてしまうと、アプリ内の関数が
            # 想定していない入力で動くことになり、審査を通した配布物の
            # 振る舞いが表の書き方で変わる。表はあくまで「外部コマンドの呼び方」。
            return None, [
                f"{where}: executor = \"builtin\" は指定できません。"
                'アプリ内蔵の変換は利用者の表からは呼べません。executor = "command" を使ってください。'
            ]
        executor = Executor.BUILTIN
    elif executor_raw == Executor.COMMAND.value:
        executor = Executor.COMMAND
    else:
        return None, [
            f"{where}: executor は \"command\" と書いてください（指定: \"{executor_raw}\"）。"
        ]

    tool = _as_str(raw.get("tool")) or ""
    if not tool:
        return None, [f"{where}: tool がありません。実行するコマンド名を書いてください。"]
    tool_problem = _tool_problem(tool)
    if tool_problem:
        return None, [f"{where}: {tool_problem}"]

    args = _as_str_list(raw.get("args"))
    if args is None:
        # 文字列 1 本で受け取ると、シェルに解釈させたくなる（= 引用符やパイプが
        # 効いてしまう）。リストしか受け取らないことで、その道を塞ぐ。
        return None, [
            f"{where}: args は文字列のリストで書いてください"
            '（例: args = ["--best", "-o", "{out}", "{in}"]）。'
        ]
    args_problem = _args_problem(args, executor)
    if args_problem:
        return None, [f"{where}: {args_problem}"]

    out_extension = _as_str(raw.get("out_extension")) or ""
    if not _EXTENSION_RE.match(out_extension):
        return None, [
            f"{where}: out_extension は英数字で書いてください"
            f'（例: out_extension = "flac"、指定: "{out_extension}"）。'
        ]

    return (
        RuleEntry(
            rule_id=rule_id,
            puid_in=puid_in,
            purpose=purpose,
            executor=executor,
            tool=tool,
            args=args,
            out_extension=out_extension,
            source=source,
            puid_out=_as_str(raw.get("puid_out")),
            format_name_out=_as_str(raw.get("format_name_out")),
        ),
        [],
    )


def _tool_problem(tool: str) -> str:
    """tool に使える名前かを見る。

    パスで書かせない。相対パスは「どこから見た相対か」がアプリの作業場所で
    変わり、`..` はその外を指せる。PATH 上（または同梱）のコマンド名だけを
    受け取り、実際にどれが動いたかは PREMIS に絶対パスで残す。
    """
    if "/" in tool or "\\" in tool:
        return (
            f'tool にパスは書けません（指定: "{tool}"）。'
            "コマンド名だけを書き、PATH が通った場所に置いてください。"
        )
    if ".." in tool:
        return f'tool に ".." は使えません（指定: "{tool}"）。'
    return ""


def _args_problem(args: list[str], executor: Executor) -> str:
    unknown = sorted(
        {
            found
            for arg in args
            for found in _PLACEHOLDER_RE.findall(arg)
            if found not in PLACEHOLDERS
        }
    )
    if unknown:
        return (
            f"args に使えない印があります: {', '.join(unknown)}。"
            "使えるのは {in}（原本）と {out}（変換先）だけです。"
        )

    if executor is Executor.BUILTIN:
        return ""

    joined = "".join(args)
    missing = [p for p in PLACEHOLDERS if p not in joined]
    if missing:
        # {out} が無ければどこにも書き出されず、{in} が無ければ原本を読まない。
        # どちらも「実行はできるが何も起きない」ので、表の時点で止める。
        return (
            f"args に {' と '.join(missing)} がありません。"
            "原本を {in}、変換先を {out} として必ず渡してください。"
        )
    return ""


# --------------------------------------------------------------------------
# 表
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RuleTable:
    """読み込みの済んだ規則表。"""

    #: (purpose, puid) -> 規則。引くのは 1 ファイルにつき 1 回なので辞書で持つ。
    rules: dict[tuple[str, str], NormalizationRule] = field(default_factory=dict)
    #: 読めなかった規則の理由。**起動を妨げず、利用者に見せる。**
    warnings: list[str] = field(default_factory=list)
    #: AIP に同梱する、実際に効いていた表そのもの（TOML）。
    document: str = ""

    def rule_for(self, puid: str | None, purpose: DerivativePurpose) -> NormalizationRule | None:
        if not puid:
            return None
        return self.rules.get((purpose.value, puid))


def parse(text: str, *, source: RuleSource) -> tuple[list[RuleEntry], list[str]]:
    """TOML の文字列を規則の並びにする。壊れていても例外にしない。

    **表が壊れていてもアプリは起動すること。** 規則表は補助的な設定であって、
    これが読めないせいで資料を受け入れられなくなるのは本末転倒。
    読めなかったものは黙って捨てず、理由を持ち帰る。
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [f"規則表の書式が壊れています（{exc}）。この表は使いません。"]

    raw_rules = data.get("rule")
    if raw_rules is None:
        return [], []
    if not isinstance(raw_rules, list):
        return [], ["規則表に [[rule]] がありません（[rule] ではなく [[rule]] と書きます）。"]

    entries: list[RuleEntry] = []
    warnings: list[str] = []
    for index, raw in enumerate(raw_rules):
        entry, problems = validate(raw, source=source, index=index)
        warnings += problems
        if entry is not None:
            entries.append(entry)
    return entries, warnings


def load(user_path: Path | None = None) -> RuleTable:
    """組み込みと利用者の表を読んで 1 つにする。

    利用者の表が無ければ組み込みだけで動く（**既定では何も設定せずに動くこと**）。
    同じ PUID に規則が重なったときは利用者が勝つ。自分の運用に合わせて
    上書きできないと、表を書ける意味が半分になる。
    """
    builtin_entries, warnings = parse(BUILTIN_TOML, source=RuleSource.BUILTIN)
    # 組み込みが検証を通らないのはアプリの不具合であって利用者には直せない。
    # ここで気づけるよう、テストで空でないことを固定してある。

    path = user_table_path() if user_path is None else user_path
    user_text = ""
    user_entries: list[RuleEntry] = []
    try:
        if path.is_file():
            user_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"規則表を読めませんでした（{path}）: {exc}")
        user_text = ""

    user_readable = False
    if user_text:
        user_entries, user_warnings = parse(user_text, source=RuleSource.USER)
        warnings += user_warnings
        # 書式ごと壊れている表は AIP に同梱しない（同梱した .toml が
        # そもそも読めないのでは、後から見た人の役に立たない）。
        user_readable = not any("書式が壊れています" in w for w in user_warnings)

    table = RuleTable(warnings=warnings)
    ids: set[str] = set()
    for entry in [*builtin_entries, *user_entries]:
        if entry.rule_id in ids:
            # id は PREMIS にそのまま書かれる。重複を許すと、AIP を読んだ人が
            # 「どちらの規則で作られたか」を決められなくなる。
            warnings.append(
                f"規則 {entry.rule_id}: 同じ id が既にあります。あとの方は使いません。"
            )
            continue
        ids.add(entry.rule_id)
        for rule in entry.to_rules():
            key = (rule.purpose.value, rule.puid_in)
            existing = table.rules.get(key)
            if existing is not None and existing.source is entry.source:
                # 同じ表の中での重複。先に書いた方を残す（あとから足した行が
                # 黙って前の行を消すより、気づける形にする）。
                warnings.append(
                    f"規則 {entry.rule_id}: {rule.puid_in} には既に "
                    f"{existing.rule_id} があります。先に書いた方を使います。"
                )
                continue
            table.rules[key] = rule

    table.document = _document(user_text if user_readable else "", warnings)
    return table


def _document(user_text: str, warnings: list[str]) -> str:
    """AIP に同梱する表を組む。

    **表そのものを AIP に入れる。** Archivematica の PREMIS は中央の FPR を
    識別子で参照する形なので、後年その FPR が引けなくなると
    「どの規則で作られたか」を書いてあっても意味を失う。表を一緒に入れておけば、
    パッケージ単体で説明が付く。

    読めなかった規則の理由もコメントとして残す。**「その規則は効かなかった」
    という事実自体が来歴である**（あるはずの変換が無い理由が、ここだけで分かる）。
    """
    lines = [
        "# この AIP を作ったときに効いていた正規化規則の表。",
        "# Archival Packager が objects/submissionDocumentation/ に書き出したもの。",
        "# 中央の登録簿を参照する形にすると、後年それが引けなくなったときに",
        "# 記録の意味が失われる。表そのものを同梱して、この AIP だけで説明が付くようにしている。",
        "",
        "# ===== 組み込みの規則（アプリに最初から入っているもの）=====",
        BUILTIN_TOML.rstrip("\n"),
        "",
    ]
    if user_text.strip():
        lines += [
            "# ===== 利用者が足した規則（rules.toml）=====",
            user_text.rstrip("\n"),
            "",
        ]
    else:
        lines += ["# 利用者が足した規則はありません。", ""]

    if warnings:
        lines.append("# ===== 読み込めなかった規則 =====")
        lines += [f"# {w}" for w in warnings]
        lines.append("")
    return "\n".join(lines)
