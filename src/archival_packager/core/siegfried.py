"""siegfried によるフォーマット識別。

現行 Swift 実装の `Sources/SIP/Siegfried.swift` に対応する。

同梱の `sf` を使って入力ツリーを再帰的に識別し、PRONOM PUID / フォーマット名 /
MIME などを取得する。シグネチャ DB（default.sig）も同梱し `-home` で参照することで
完全オフラインで動作させる。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import bundled
from .models import SIPPipelineError


@dataclass(slots=True)
class SiegfriedRecord:
    """1 ファイル分の識別結果。"""

    puid: str | None = None  # PRONOM ID（例: fmt/19）。UNKNOWN/空は None。
    format_name: str | None = None  # 例: "Acrobat PDF 1.4 - Portable Document Format"
    mime_type: str | None = None  # 例: application/pdf
    basis: str | None = None  # 識別根拠（extension match; byte match ...）
    warning: str | None = None  # 例: "extension mismatch" / 未識別


def identify(
    root: Path,
    *,
    tool: Path | None = None,
    home: Path | None = None,
    timeout: float | None = None,
) -> dict[str, SiegfriedRecord]:
    """root 配下を再帰識別し、siegfried が返すパス文字列 -> 識別結果 の辞書を返す。

    Args:
        root: 走査するディレクトリ。
        tool: sf の場所。省略時は同梱バイナリを解決する。
        home: シグネチャ home。省略時は同梱の default.sig を置いたディレクトリ。
        timeout: 秒。省略時は無制限（大きなツリーで途中終了させないため）。
    """
    tool = tool or bundled.find("sf")
    if tool is None:
        raise SIPPipelineError.tool_not_found("siegfried (sf)")
    bundled.ensure_executable(tool)

    args = [str(tool)]
    sig_home = home if home is not None else bundled.signature_home()
    if sig_home is not None:
        args += ["-home", str(sig_home)]
    args += ["-json", str(root)]

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            # siegfried の出力は UTF-8。日本語ファイル名が壊れた場合でも
            # 例外にせず置換して先へ進める（1 ファイルのために全体を止めない）。
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise SIPPipelineError.tool_failed("siegfried", -1, str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise SIPPipelineError.tool_failed("siegfried", -1, f"タイムアウト: {exc}") from exc

    if proc.returncode != 0:
        raise SIPPipelineError.tool_failed("siegfried", proc.returncode, (proc.stderr or "")[:500])

    try:
        output = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SIPPipelineError.tool_failed("siegfried", 0, f"JSON 解析に失敗: {exc}") from exc

    return _records_from(output)


def _records_from(output: object) -> dict[str, SiegfriedRecord]:
    """siegfried の -json 出力を SiegfriedRecord へ変換する。

    欠落や空文字に対して頑健にする（Swift 版が decodeIfPresent で全項目を
    optional にしているのと同じ方針）。siegfried のバージョン差でフィールドが
    増減しても、識別できたものだけを拾って先へ進む。
    """
    if not isinstance(output, dict):
        return {}

    result: dict[str, SiegfriedRecord] = {}
    for entry in output.get("files") or []:
        if not isinstance(entry, dict):
            continue
        filename = entry.get("filename")
        if not filename:
            continue

        matches = [m for m in (entry.get("matches") or []) if isinstance(m, dict)]
        # pronom 名前空間のマッチを優先。無ければ先頭マッチにフォールバック。
        chosen = next((m for m in matches if m.get("ns") == "pronom"), None)
        if chosen is None:
            chosen = matches[0] if matches else {}

        result[filename] = SiegfriedRecord(
            puid=_normalized_puid(chosen.get("id")),
            format_name=_non_empty(chosen.get("format")),
            mime_type=_non_empty(chosen.get("mime")),
            basis=_non_empty(chosen.get("basis")),
            warning=_non_empty(chosen.get("warning")),
        )
    return result


def _non_empty(value: object) -> str | None:
    """空文字を None に正規化する。"""
    if isinstance(value, str) and value:
        return value
    return None


def _normalized_puid(value: object) -> str | None:
    """PUID を正規化する。空文字・"UNKNOWN" は None 扱い。"""
    if not isinstance(value, str) or not value:
        return None
    if value.casefold() == "unknown":
        return None
    return value
