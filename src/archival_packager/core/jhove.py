"""JHOVE による形式の適合性検査（変換で作った派生物と、原本の TIFF・PDF が対象）。

識別（これは何形式か）は siegfried が答える。ここで答えるのは
**「その形式の仕様に適合しているか」**で、Archivematica が変換後の派生物に
対して行っている検査に相当する。

## 同梱の構成

JHOVE は Java 製なので、Java の実行環境ごと同梱する。

    bin/jre/      jlink で必要なモジュール（java.base / java.desktop /
                  java.logging）だけに絞った Temurin 21。約 50MB
    bin/jhove/    JHOVE 本体と TIFF・PDF の検査部品（jar 4 本、約 1.3MB）

取得は scripts/fetch-binaries.*。版は両OSで揃える。

## 判定の扱い

JHOVE の status は 3 通りに分けて読む。

    Well-Formed and valid        → 適合
    Well-Formed, but not valid   → 不適合（形は保っているが仕様に反する）
    Not well-formed              → 不適合（形からして壊れている）

これ以外（"Unknown" など）は**判定できなかった**として扱い、不適合とは
言わない。たとえばファイルを読めなかったときの "Unknown" を不適合と
読むと、健全な派生物を捨ててしまう。

## ファイル名を JHOVE に渡さない

派生物は短い ASCII の名前（0.tif, 1.tif …）でハードリンクし、その
ディレクトリを作業ディレクトリにして相対パスで渡す。理由は 2 つ。

- Windows の Java は、コマンドライン引数を ANSI コードページ（日本語環境なら
  CP932）で受け取る。CP932 に無い文字を含む名前は、引数の段階で壊れる。
  作業ディレクトリは Unicode の API で取るので、こちらは壊れない
- Windows のコマンドラインには約 32,000 文字の上限がある。長い日本語の
  パスを何百件も並べると超える

## 1 回の起動でまとめて検査する

Java の起動には 1 回 0.15〜0.6 秒かかる（2026-10-27、M 系 Mac で実測）。
ファイルごとに起動すると、画像 1 万件で 30 分前後が起動だけに消える。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from . import bundled

#: 拡張子ごとの検査部品。**ここに無い形式は検査しない**（呼び出し側が「未検査」と記録する）。
MODULE_FOR_EXTENSION = {"tif": "TIFF-hul", "tiff": "TIFF-hul", "pdf": "PDF-hul"}

#: 検査部品の名前 → 設定ファイルに書く Java のクラス名。
_MODULE_CLASSES = {
    "TIFF-hul": "edu.harvard.hul.ois.jhove.module.TiffModule",
    "PDF-hul": "edu.harvard.hul.ois.jhove.module.PdfModule",
}

VALID = "Well-Formed and valid"
NOT_VALID = "Well-Formed, but not valid"
NOT_WELL_FORMED = "Not well-formed"

_NS = "{http://schema.openpreservation.org/ois/xml/ns/jhove}"

#: 1 回の起動で渡す件数の上限。名前は短いので、文字数の上限より先に
#: 出力の XML の大きさが気になる（1 件あたり数十 KB になることがある）。
_CHUNK = 200

#: 設定ファイル。名前空間と schemaLocation は JHOVE が同梱している
#: jhoveConfig.xsd に合わせる。合っていないと、検査はできるが標準エラーに
#: スキーマ違反が大量に出る（2026-10-27 に踏んだ）。schemaLocation の URL は
#: JHOVE が jar 内の xsd に読み替えるので、ネットワークには出ない。
_CONFIG = """<?xml version="1.0" encoding="UTF-8"?>
<jhoveConfig version="1.0"
 xmlns="http://hul.harvard.edu/ois/xml/ns/jhove/jhoveConfig"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:schemaLocation="http://hul.harvard.edu/ois/xml/ns/jhove/jhoveConfig http://hul.harvard.edu/ois/xml/xsd/jhove/1.4/jhoveConfig.xsd">
 <jhoveHome>.</jhoveHome>
 <defaultEncoding>utf-8</defaultEncoding>
 <tempDirectory>.</tempDirectory>
 <bufferSize>131072</bufferSize>
 <mixVersion>2.0</mixVersion>
{modules}
</jhoveConfig>
"""


class JhoveUnavailable(Exception):
    """JHOVE を動かせなかった（同梱されていない、起動しない、出力が読めない）。

    **個々のファイルの不適合とは別物。** これを不適合として扱うと、
    道具の不調で健全な派生物を捨てることになる。
    """


@dataclass(slots=True)
class JhoveResult:
    """1 ファイル分の検査結果。"""

    status: str
    #: 検査部品と版（例 "TIFF-hul 1.9.5"）。
    module: str
    format: str | None = None
    version: str | None = None
    #: JHOVE のメッセージ（"error: Invalid strip offset (TIFF-HUL-29)" の形）。
    messages: list[str] = field(default_factory=list)

    @property
    def conforms(self) -> bool | None:
        """適合なら True、不適合なら False、判定できなかったなら None。"""
        if self.status == VALID:
            return True
        if self.status in (NOT_VALID, NOT_WELL_FORMED):
            return False
        return None


@dataclass(slots=True)
class Tool:
    java: Path
    classpath: list[Path]


def locate() -> Tool | None:
    """同梱の Java と JHOVE を返す。どちらかが欠けていれば None。"""
    directory = bundled.bin_dir()
    if directory is None:
        return None
    java = directory / "jre" / "bin" / ("java.exe" if bundled.IS_WINDOWS else "java")
    jars = sorted((directory / "jhove").glob("*.jar"))
    if not java.is_file() or not jars:
        return None
    return Tool(java, jars)


def validate(
    paths: list[Path],
    scratch_parent: Path,
    *,
    tool: Tool | None = None,
    timeout: float | None = None,
) -> tuple[dict[Path, JhoveResult], str]:
    """paths を検査し、(パス → 結果, "JHOVE <版>") を返す。

    拡張子が MODULE_FOR_EXTENSION に無いものは結果に含めない。

    Args:
        scratch_parent: ハードリンクを張る作業場所を作る親。**検査対象と同じ
            ボリュームにする**（違うとハードリンクが張れず、コピーになる）。

    Raises:
        JhoveUnavailable: 同梱が無い、起動しない、出力が読めない。
    """
    tool = tool or locate()
    if tool is None:
        raise JhoveUnavailable("JHOVE が同梱されていません")
    try:
        bundled.ensure_executable(tool.java)
    except OSError as exc:
        # 実行ビットを付け直せない、または Java が消えている。
        # ここで素の OSError を投げると、呼び出し側が移管ごと止まる。
        raise JhoveUnavailable(f"同梱の Java を実行できません: {exc}") from exc

    groups: dict[str, list[Path]] = {}
    for path in paths:
        module = MODULE_FOR_EXTENSION.get(path.suffix.lstrip(".").lower())
        if module is not None:
            groups.setdefault(module, []).append(path)

    results: dict[Path, JhoveResult] = {}
    release = ""
    for module, members in groups.items():
        for start in range(0, len(members), _CHUNK):
            chunk = members[start:start + _CHUNK]
            found, release = _run(tool, module, chunk, scratch_parent, timeout)
            results.update(found)
    return results, f"JHOVE {release}" if release else "JHOVE"


def _run(
    tool: Tool,
    module: str,
    paths: list[Path],
    scratch_parent: Path,
    timeout: float | None,
) -> tuple[dict[Path, JhoveResult], str]:
    try:
        scratch = Path(tempfile.mkdtemp(prefix=".jhove-", dir=scratch_parent))
    except OSError as exc:
        raise JhoveUnavailable(f"検査用の作業場所を作れません: {exc}") from exc
    try:
        names: dict[str, Path] = {}
        for index, path in enumerate(paths):
            name = f"{index}{path.suffix.lower()}"
            try:
                _link_or_copy(path, scratch / name)
            except OSError as exc:
                # 素の OSError を投げると、移管ごと止まる。検査できなかったとして返す。
                raise JhoveUnavailable(f"検査用の写しを作れません: {path.name}: {exc}") from exc
            names[name] = path

        modules = "\n".join(
            f" <module><class>{cls}</class></module>" for cls in _MODULE_CLASSES.values()
        )
        (scratch / "jhove.conf").write_text(_CONFIG.format(modules=modules), encoding="utf-8")

        args = [
            str(tool.java),
            # 画面を出さない。java.desktop は TIFF の検査部品が使うので外せないが、
            # 何も表示しないことを明示しておくと、macOS で Dock にアイコンが出ない。
            "-Djava.awt.headless=true",
            "-cp", os.pathsep.join(str(jar) for jar in tool.classpath),
            "edu.harvard.hul.ois.jhove.Jhove",
            "-c", "jhove.conf", "-h", "xml", "-m", module,
            *names,
        ]
        try:
            proc = subprocess.run(
                args, cwd=scratch, capture_output=True, timeout=timeout,
                **bundled.no_window(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            # 起動できない（Intel Mac に arm64 の Java を入れた場合など）。
            raise JhoveUnavailable(f"JHOVE を起動できません: {exc}") from exc

        stderr = proc.stderr.decode("utf-8", "replace").strip()
        if proc.returncode != 0:
            raise JhoveUnavailable(f"JHOVE が異常終了しました (rc={proc.returncode}): {stderr[:300]}")
        try:
            parsed, release = parse_output(proc.stdout)
        except ValueError as exc:
            raise JhoveUnavailable(f"JHOVE の出力を読めません: {exc}; {stderr[:300]}") from exc
        return {names[name]: result for name, result in parsed.items() if name in names}, release
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _link_or_copy(src: Path, dest: Path) -> None:
    try:
        os.link(src, dest)
    except OSError:
        # ハードリンクを張れないファイルシステム（FAT 系の外付けなど）。
        shutil.copyfile(src, dest)


def parse_output(xml: bytes) -> tuple[dict[str, JhoveResult], str]:
    """JHOVE の XML 出力を読む。(渡した名前 → 結果, JHOVE の版) を返す。

    Raises:
        ValueError: XML として読めない、または JHOVE の出力ではない。
    """
    try:
        root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as exc:
        raise ValueError(str(exc)) from exc
    if root.tag != f"{_NS}jhove":
        raise ValueError(f"想定と違う根要素: {root.tag}")

    results: dict[str, JhoveResult] = {}
    for info in root.iter(f"{_NS}repInfo"):
        # 名前は URI として出てくる（空白が %20 になる）。渡す名前は数字と
        # 拡張子だけなので、そのまま突き合わせられる。
        uri = info.get("uri") or ""
        reporting = info.find(f"{_NS}reportingModule")
        module = ""
        if reporting is not None:
            module = f"{reporting.text or ''} {reporting.get('release') or ''}".strip()
        messages = [
            f"{m.get('severity') or 'info'}: {(m.text or '').strip()}"
            + (f" ({m.get('id')})" if m.get("id") else "")
            for m in info.iter(f"{_NS}message")
        ]
        results[uri] = JhoveResult(
            status=(info.findtext(f"{_NS}status") or "").strip(),
            module=module,
            format=info.findtext(f"{_NS}format"),
            version=info.findtext(f"{_NS}version"),
            messages=messages,
        )
    return results, root.get("release") or ""
