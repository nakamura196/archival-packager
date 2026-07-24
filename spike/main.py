"""Flet 実行可能性検証スパイク。

目的は 3 点だけを事実で確かめること。捨てる前提のコード。
  1. 同梱した siegfried(sf) を、パッケージ後のアプリから起動できるか
  2. 生成された .app に Developer ID 署名＋公証が通るか
  3. 同じソースから Windows 版がビルドできるか

バイナリの置き場所について
--------------------------
当初は Python アプリ側（assets/bin/）に置いていた。実行はできたが、公証で
弾かれた（Apple は app.zip の中まで検査し、Developer ID 署名・セキュアタイムスタンプ・
hardened runtime を要求する。zip 内のデータは署名できない）。

そのため .app のバンドル内 Contents/Resources/bin/ へ移し、個別に署名する構成にした。
Windows 側の配置は未知なので、既知の場所を先に当たり、外れたら総当たりで探して
「実際に見つかった場所」を報告する。
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

import flet as ft

IS_WINDOWS = platform.system() == "Windows"
BIN_NAME = "sf.exe" if IS_WINDOWS else "sf"


def known_locations() -> list[tuple[str, Path]]:
    """配置が分かっている場所。ここで当たるのが正常。"""
    found: list[tuple[str, Path]] = []
    exe = Path(sys.executable).resolve()

    if not IS_WINDOWS:
        # macOS: <App>.app/Contents/MacOS/<exe> から見て Contents/Resources/bin
        found.append(("bundle:Contents/Resources/bin", exe.parent.parent / "Resources" / "bin"))
    else:
        # Windows: 実行ファイルと同階層 / data 配下を想定（CI で実測して確定させる）
        found.append(("bundle:exe隣", exe.parent / "bin"))
        found.append(("bundle:data/bin", exe.parent / "data" / "bin"))
        found.append(("bundle:data/flutter_assets/bin", exe.parent / "data" / "flutter_assets" / "bin"))

    # 開発モード（flet run）: リポジトリ直下の binaries/<os>/
    repo = Path(__file__).resolve().parent.parent
    osdir = "windows" if IS_WINDOWS else "macos"
    found.append(("dev:binaries", repo / "binaries" / osdir))

    return found


def exhaustive_roots() -> list[tuple[str, Path]]:
    """既知の場所で外れたときの総当たり候補。Windows の配置を突き止めるため。"""
    roots: list[tuple[str, Path]] = []
    here = Path(__file__).resolve().parent
    exe_dir = Path(sys.executable).resolve().parent

    for label, base in (("__file__", here), ("sys.executable", exe_dir), ("cwd", Path.cwd())):
        roots.append((label, base))
        for up in range(1, 6):
            parent = base
            for _ in range(up):
                parent = parent.parent
            roots.append((f"{label}/..{up}", parent))

    for key in ("FLET_APP_STORAGE_DATA", "FLET_APP_STORAGE_TEMP", "FLET_ASSETS_DIR"):
        value = os.environ.get(key)
        if value:
            roots.append((key, Path(value)))

    return roots


def find_sf() -> tuple[Path | None, list[str]]:
    """sf を探す。見つかった Path と探索ログを返す。"""
    log: list[str] = []

    for label, directory in known_locations():
        target = directory / BIN_NAME
        if target.is_file():
            log.append(f"HIT  [{label}] {target}")
            return target, log
        log.append(f"miss [{label}] {directory}")

    log.append("--- 既知の場所で外れたため総当たり ---")
    seen: set[Path] = set()
    for label, root in exhaustive_roots():
        if not root.exists():
            continue
        for sub in ("", "bin", "assets/bin", "Resources/bin", "data/bin", "flutter_assets/bin"):
            target = ((root / sub / BIN_NAME) if sub else (root / BIN_NAME)).resolve()
            if target in seen:
                continue
            seen.add(target)
            if target.is_file():
                log.append(f"HIT  [{label}/{sub or '.'}] {target}")
                return target, log

    log.append(f"MISS 候補 {len(seen)} 件すべてに {BIN_NAME} なし")
    return None, log


def run_sf(sf: Path) -> str:
    """sf を起動する。実行権限が落ちていれば付け直してから再試行する。

    同梱の default.sig を -sig で明示的に渡す。これを省くと siegfried は
    OS 既定の場所を探しに行き、開発機では Homebrew 版の署名 DB を拾ってしまう。
    配布先にはそれが無いため、指定を省いた実装は開発機でだけ動く。
    """
    sig = sf.parent / "default.sig"
    args = [str(sf), "-version"]
    if sig.is_file():
        args = [str(sf), "-sig", str(sig), "-version"]

    def invoke() -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=30)

    note = f"default.sig: {'同梱を使用' if sig.is_file() else '見つからず（OS既定にフォールバック）'}\n"
    try:
        proc = invoke()
    except PermissionError:
        sf.chmod(0o755)
        proc = invoke()
        return f"{note}(chmod 755 後に成功)\n{proc.stdout}{proc.stderr}"

    return f"{note}{proc.stdout}{proc.stderr}"


def report_path() -> Path:
    """検証結果の書き出し先。

    パッケージ後の GUI アプリは画面を見ないと結果が分からないが、それでは
    CI で判定できず、開発機でも目視に頼ることになる。同じ内容をファイルにも
    落として、機械で合否を採れるようにする。
    """
    override = os.environ.get("SPIKE_REPORT")
    if override:
        return Path(override)
    return Path.home() / "flet-spike-report.txt"


def main(page: ft.Page) -> None:
    page.title = "Flet 実行可能性検証"
    page.window.width = 900
    page.window.height = 700
    page.scroll = ft.ScrollMode.AUTO

    result = ft.Text("", selectable=True, font_family="monospace", size=12)

    env_lines = [f"{k}={v}" for k, v in sorted(os.environ.items()) if k.startswith("FLET")]
    env_text = "\n".join(env_lines) if env_lines else "(FLET_* 環境変数なし)"

    def probe(_: ft.ControlEvent | None) -> None:
        sf, log = find_sf()
        parts = [
            f"platform      : {platform.system()} {platform.machine()}",
            f"python        : {sys.version.split()[0]}",
            f"sys.executable: {sys.executable}",
            f"__file__      : {Path(__file__).resolve()}",
            f"cwd           : {Path.cwd()}",
            "",
            "--- FLET 環境変数 ---",
            env_text,
            "",
            "--- sf 探索 ---",
            *log,
            "",
        ]
        if sf is None:
            parts.append("結果: NG 同梱バイナリを解決できず")
        else:
            try:
                parts.append("--- sf -version ---")
                parts.append(run_sf(sf))
                parts.append("結果: OK 同梱バイナリの起動に成功")
            except Exception as exc:  # noqa: BLE001 — 何が起きたかを画面に出すのが目的
                parts.append(f"結果: NG 起動に失敗: {type(exc).__name__}: {exc}")

        text = "\n".join(parts)
        result.value = text
        page.update()

        try:
            report_path().write_text(text, encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            result.value = f"{text}\n\n(レポート書き出し失敗: {exc})"
            page.update()

        if os.environ.get("SPIKE_AUTOQUIT") == "1":
            page.window.close()

    page.add(
        ft.Text("同梱バイナリ解決テスト", size=20, weight=ft.FontWeight.BOLD),
        ft.Text("パッケージ後のアプリから siegfried(sf) を起動できるかを確かめる。"),
        ft.FilledButton("sf を探して実行", on_click=probe),
        ft.Divider(),
        result,
    )
    probe(None)  # 起動直後に自動実行（ビルド版をダブルクリックしただけで結果が見える）


if __name__ == "__main__":
    ft.app(main)
