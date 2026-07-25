"""正規化の実行。

現行 Swift 実装の `Sources/AIP/Normalizer.swift` に対応する。
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

from . import bundled
from .aip_models import (
    AIPFile,
    AIPPipelineError,
    Derivative,
    DerivativePurpose,
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


def normalize(file: AIPFile, rule: NormalizationRule, work_dir: Path) -> Derivative:
    """ルールを適用して派生物を返す。

    ツールが見つからない/変換失敗時は送出する（呼び出し側で警告にして
    AIP 化自体は続行する。1 ファイルの変換失敗で移管全体を止めない）。
    """
    tool_path = locate(rule.tool)
    if tool_path is None:
        raise AIPPipelineError.tool_not_found(rule.tool)
    bundled.ensure_executable(tool_path)

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
        )
    except OSError as exc:
        raise AIPPipelineError.tool_failed(rule.tool, -1, str(exc)) from exc

    if proc.returncode != 0:
        raise AIPPipelineError.tool_failed(rule.tool, proc.returncode, (proc.stderr or "")[:500])

    if not out_path.is_file():
        # 終了コード 0 でも出力が無いことがある（引数の解釈違いなど）。
        # 存在しない派生物を PREMIS に記録してしまわないよう、ここで止める。
        raise AIPPipelineError.tool_failed(rule.tool, 0, "出力が生成されませんでした")

    return Derivative(
        purpose=DerivativePurpose.PRESERVATION,
        path=out_path,
        relative_path=derivative_relative_path(file.relative_path, rule.out_extension),
        size_bytes=out_path.stat().st_size,
        uuid=derivative_uuid,
        tool_name=rule.tool,
        command_line=" ".join([rule.tool, *args]),
        sha256=sha256_of(out_path),
        puid_out=rule.puid_out,
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
