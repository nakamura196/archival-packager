"""Flet 実行可能性検証スパイク。

目的は 3 点だけを事実で確かめること。捨てる前提のコード。
  1. 同梱した siegfried(sf) を、パッケージ後のアプリから起動できるか
  2. 生成された .app に Developer ID 署名＋公証が通るか
  3. 同じソースから Windows 版がビルドできるか

パッケージ後は Python が bundle 内へ展開されるため、開発時と実行時で
バイナリの在処が変わる。どこに置かれるかを推測せず、候補を総当たりして
「実際に見つかった場所」を画面に出す。
"""

import os
import platform
import subprocess
import sys
from pathlib import Path

import flet as ft

BIN_NAME = "sf.exe" if platform.system() == "Windows" else "sf"


def candidate_roots() -> list[tuple[str, Path]]:
    """sf を探す候補ディレクトリを、由来のラベル付きで列挙する。"""
    roots: list[tuple[str, Path]] = []

    here = Path(__file__).resolve().parent
    roots.append(("__file__", here))
    roots.append(("__file__/assets", here / "assets"))

    exe_dir = Path(sys.executable).resolve().parent
    roots.append(("sys.executable", exe_dir))

    roots.append(("cwd", Path.cwd()))

    # Flet がパッケージ時に設定する環境変数。実機で何が入るかは実測する。
    for key in ("FLET_APP_STORAGE_DATA", "FLET_APP_STORAGE_TEMP", "FLET_ASSETS_DIR"):
        value = os.environ.get(key)
        if value:
            roots.append((key, Path(value)))

    # 上位ディレクトリも辿る。.app 内は Contents/ 以下が深いため。
    for label, base in list(roots):
        for up in range(1, 6):
            parent = base
            for _ in range(up):
                parent = parent.parent
            roots.append((f"{label}/..{up}", parent))

    return roots


def find_bundled_sf() -> tuple[Path | None, list[str]]:
    """同梱 sf を探す。見つかった Path と、探索ログを返す。"""
    log: list[str] = []
    seen: set[Path] = set()

    for label, root in candidate_roots():
        if not root.exists():
            continue
        for sub in ("", "bin", "assets/bin", "flutter_assets/bin", "app/bin"):
            target = (root / sub / BIN_NAME) if sub else (root / BIN_NAME)
            target = target.resolve()
            if target in seen:
                continue
            seen.add(target)
            if target.is_file():
                log.append(f"HIT  [{label}] {target}")
                return target, log

    log.append(f"MISS 候補 {len(seen)} 件すべてに {BIN_NAME} なし")
    return None, log


def run_sf(sf: Path) -> str:
    """sf を起動する。実行権限が落ちていれば付け直してから再試行する。"""
    def invoke() -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(sf), "-version"],
            capture_output=True,
            text=True,
            timeout=30,
        )

    try:
        proc = invoke()
    except PermissionError:
        # パッケージ経路で実行ビットが落ちるのは想定内。復旧できるかを確かめる。
        sf.chmod(0o755)
        proc = invoke()
        return f"(chmod 755 後に成功)\n{proc.stdout}{proc.stderr}"

    return f"{proc.stdout}{proc.stderr}"


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

    def probe(_: ft.ControlEvent) -> None:
        sf, log = find_bundled_sf()
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
            parts.append("結果: ❌ 同梱バイナリを解決できず")
        else:
            try:
                parts.append("--- sf -version ---")
                parts.append(run_sf(sf))
                parts.append("結果: ✅ 同梱バイナリの起動に成功")
            except Exception as exc:  # noqa: BLE001 — 何が起きたかを画面に出すのが目的
                parts.append(f"結果: ❌ 起動に失敗: {type(exc).__name__}: {exc}")

        text = "\n".join(parts)
        result.value = text
        page.update()

        # 画面と同じ内容をファイルへ。書けなくても検証自体は続行する。
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
