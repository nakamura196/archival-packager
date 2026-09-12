"""正規化の実行。

退役した Swift 実装の `Sources/AIP/Normalizer.swift` に由来する。
1 原本に NormalizationRule を適用し、保存用の派生物を生成する。
**原本は読むだけで変更しない**（保存対象そのものを書き換えてはならない）。

生成物は作業ディレクトリに `<uuid>.<ext>` として書き、AIPBuilder が objects/ へ配置する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid as _uuid
from pathlib import Path

from . import bundled, image_normalize
from .aip_models import (
    AIPFile,
    AIPPipelineError,
    Derivative,
    DerivativePurpose,
    Executor,
    NormalizationRule,
)
from .checksums import sha256_of


def locate(tool: str) -> Path | None:
    """変換ツールを探す。

    解決順は 同梱 -> PATH。Swift 版は /usr/bin/sips や Homebrew の固定パスも
    見ていたが、それは macOS 固有の事情なので持ち込まない。同梱を第一とし、
    開発時の利便のために PATH も見る。
    """
    found = bundled.find(tool)
    if found is not None:
        return found
    which = shutil.which(tool)
    return Path(which) if which else None


#: 版を聞ける外部ツールと、そのための引数。
#:
#: **同梱していないツールだけを相手にする。** gs は利用者の環境から来るので、
#: どの版が動いたかは配布物からは決まらない。記録しておかないと、あとから
#: 「この PDF は何で作られたのか」を追う手がかりが残らない。同梱しているものは
#: アプリの版から辿れるので、わざわざプロセスを起こして聞かない。
#:
#: 知らないツールを勝手に `--version` で叩かないのは、引数の意味が分からない
#: 相手に何をさせることになるか分からないため（変換対象を上書きしかねない）。
_VERSION_PROBES: dict[str, list[str]] = {"gs": ["--version"]}

#: 版の表示名。`gs --version` は "10.07.1" としか答えないので、何の版かを添える。
_VERSION_LABELS: dict[str, str] = {"gs": "Ghostscript"}

#: 1 回聞いた版を使い回す入れ物。1 回の移管で何十件も変換するのに、
#: そのたびにプロセスを起こす理由がない。キーは解決したパス
#: （同じ "gs" でも同梱版と PATH 上の版で中身が違う）。
_version_cache: dict[str, str] = {}


def tool_version(tool: str, tool_path: Path) -> str:
    """外部ツールの版を返す。聞けなければ空文字。

    **版が取れないことは変換の失敗ではない。** ここで例外を投げると、
    記録が少し薄くなるだけの事情で AIP が作れなくなる。gs が古くて
    `--version` を解さない、実行はできるが壊れている、といった場合も
    黙って空を返し、変換そのものは試す。
    """
    probe = _VERSION_PROBES.get(tool)
    if probe is None:
        return ""

    key = str(tool_path)
    if key in _version_cache:
        return _version_cache[key]

    version = _probe_version(tool_path, probe)
    if version:
        version = f"{_VERSION_LABELS.get(tool, tool)} {version}"
    _version_cache[key] = version
    return version


def _probe_version(tool_path: Path, probe: list[str]) -> str:
    try:
        proc = subprocess.run(
            [str(tool_path), *probe],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            # 応答しないツールでパイプライン全体を止めない。版を聞くだけなので
            # 待つ意味も無い。
            timeout=15,
            **bundled.no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    if proc.returncode != 0:
        return ""

    # gs は版だけを 1 行返すが、他のツールが複数行返しても困らないようにする。
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    return lines[0] if lines else ""


def event_detail(derivative: Derivative) -> str:
    """PREMIS の eventDetail に入れる 1 行を組む。

    Archivematica は

        ArchivematicaFPRCommandID="a34ddc9b-..."; program="convert"; version="ImageMagick 6.9.7-4"

    のように「どの規則が・どの道具の・どの版で」動いたかを書く。同じ形にする。
    3 つのどれが欠けても、**この派生物を別の環境で作り直せるかどうかを
    後から判断できない**（規則が分からなければ何をしたか分からず、版が
    分からなければ同じバイト列になるか分からない）。
    """
    version = derivative.tool_version or "unknown"  # 空欄だと「聞き忘れ」と読めてしまう
    parts = [f'rule="{derivative.rule_id}"']
    # 規則が組み込みか、利用者が足したものかを別の属性で書く。
    # **rule="image-to-tiff(user)" のように識別子へ混ぜない。** 識別子は過去の
    # AIP にそのまま書き込まれている文字列で、後年それと突き合わせるためにある。
    # 装飾を足すと、同じ規則で作った古い AIP と新しい AIP で値が食い違い、
    # 突き合わせに文字列の加工が要るようになる。属性を 1 つ増やす方が、
    # 既存の読み手（rule= だけを見る側）も壊さない。
    if derivative.rule_source:
        parts.append(f'ruleSource="{derivative.rule_source}"')
    parts += [
        f'program="{derivative.tool_name}"',
        f'version="{version}"',
    ]
    if derivative.command_line:
        parts.append(derivative.command_line)
    return "; ".join(parts)


def normalize(file: AIPFile, rule: NormalizationRule, work_dir: Path) -> Derivative:
    """ルールを適用して派生物を返す。

    ツールが見つからない/変換失敗時は送出する（呼び出し側で警告にして
    AIP 化自体は続行する。1 ファイルの変換失敗で移管全体を止めない）。
    """
    # **tool の名前で分岐しない。** 利用者が tool = "pillow" という外部コマンドの
    # 規則を書いたときに、アプリ内蔵の画像変換が代わりに動いてはならない。
    # 内蔵処理を呼ぶ道は executor = "builtin" だけで、それは利用者の表からは
    # 指定できない（rule_table.validate が拒む）。
    if rule.executor is Executor.BUILTIN:
        return _normalize_in_process(file, rule, work_dir)
    return _normalize_by_subprocess(file, rule, work_dir)


def _normalize_in_process(
    file: AIPFile, rule: NormalizationRule, work_dir: Path
) -> Derivative:
    """外部プロセスを使わない変換（画像 → TIFF）。

    同梱バイナリの探索も PATH も要らないので、配布先で「ツールが無くて変換
    されなかった」が起きない。
    """
    if rule.tool != image_normalize.TOOL:
        # 組み込みの表にしか現れない道なので、ここに来るのはアプリの不具合。
        # 黙って画像変換にかけると、規則が言っていない変換が行われる。
        raise AIPPipelineError.tool_not_found(rule.tool)

    derivative_uuid = str(_uuid.uuid4())
    out_path = work_dir / f"{derivative_uuid}.{rule.out_extension}"
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        detail = image_normalize.to_tiff(file.absolute_path, out_path)
    except OSError as exc:
        # 壊れた画像・未対応のサブフォーマットはここに来る。落とさず警告にする。
        raise AIPPipelineError.tool_failed(rule.tool, -1, str(exc)) from exc

    # 版は tool_version に持たせるので、説明文の先頭に付いている版は外す。
    # eventDetail に同じ版が 2 度出ると、読む側が「別の版の話か」と迷う。
    version = image_normalize.version_note()
    return _derivative(file, rule, out_path, derivative_uuid,
                       tool_name=rule.tool, tool_version=version,
                       command_line=detail.removeprefix(f"{version}: "))


def _normalize_by_subprocess(
    file: AIPFile, rule: NormalizationRule, work_dir: Path
) -> Derivative:
    tool_path = locate(rule.tool)
    if tool_path is None:
        raise AIPPipelineError.tool_not_found(rule.tool)
    bundled.ensure_executable(tool_path)
    version = tool_version(rule.tool, tool_path)

    derivative_uuid = str(_uuid.uuid4())
    out_path = work_dir / f"{derivative_uuid}.{rule.out_extension}"
    work_dir.mkdir(parents=True, exist_ok=True)

    args = [
        token.replace("{in}", str(file.absolute_path)).replace("{out}", str(out_path))
        for token in rule.args
    ]

    env = _tool_environment(tool_path, rule.tool)

    try:
        proc = subprocess.run(
            [str(tool_path), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **bundled.no_window(),
        )
    except OSError as exc:
        raise AIPPipelineError.tool_failed(rule.tool, -1, str(exc)) from exc

    if proc.returncode != 0:
        raise AIPPipelineError.tool_failed(rule.tool, proc.returncode, (proc.stderr or "")[:500])

    if not out_path.is_file():
        # 終了コード 0 でも出力が無いことがある（引数の解釈違いなど）。
        # 存在しない派生物を PREMIS に記録してしまわないよう、ここで止める。
        raise AIPPipelineError.tool_failed(rule.tool, 0, "出力が生成されませんでした")

    return _derivative(file, rule, out_path, derivative_uuid,
                       tool_name=rule.tool, tool_version=version,
                       command_line=" ".join([rule.tool, *args]))


def _derivative(
    file: AIPFile,
    rule: NormalizationRule,
    out_path: Path,
    derivative_uuid: str,
    *,
    tool_name: str,
    command_line: str,
    tool_version: str = "",
) -> Derivative:
    if not out_path.is_file():
        raise AIPPipelineError.tool_failed(rule.tool, 0, "出力が生成されませんでした")

    return Derivative(
        purpose=DerivativePurpose.PRESERVATION,
        path=out_path,
        relative_path=derivative_relative_path(file.relative_path, rule.out_extension),
        size_bytes=out_path.stat().st_size,
        uuid=derivative_uuid,
        tool_name=tool_name,
        tool_version=tool_version,
        rule_id=rule.rule_id,
        rule_source=rule.source.value,
        command_line=command_line,
        sha256=sha256_of(out_path),
        puid_out=rule.puid_out,
        format_name_out=rule.format_name_out,
    )


def derivative_relative_path(original_rel: str, ext: str) -> str:
    """原本と同じディレクトリに "<basename>-preservation.<ext>" を置く相対パスを作る。

    原本を上書きしないこと、および原本との対応が名前から読めることが要件。
    """
    parts = original_rel.rsplit("/", 1)
    directory = parts[0] + "/" if len(parts) == 2 else ""
    name = parts[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{directory}{stem}-preservation.{ext}"


def _tool_environment(tool_path: Path, tool: str) -> dict[str, str] | None:
    """同梱ツールに必要な環境変数を組む。

    同梱 Ghostscript は初期化 PostScript とフォントを隣接ディレクトリから読む。
    GS_LIB を向けないと、開発機ではシステムの gs 資産を拾って動き、配布先で
    初期化に失敗する（siegfried の default.sig と同種の落とし穴）。
    システムの gs を使う場合は何もしない（gs 自身の既定パスに任せる）。
    """
    if tool != "gs":
        return None

    share = tool_path.parent / "gs-share"
    if not share.is_dir():
        return None

    env = dict(os.environ)
    env["GS_LIB"] = str(share)
    return env
