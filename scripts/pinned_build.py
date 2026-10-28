"""uv.lock どおりの版で flet build を回す。

なぜ要るか（Issue #20）:
    flet build は uv.lock を読まない。pyproject の [project].dependencies の範囲
    （例: pypdf>=5.1.0）をそのまま pip に渡すので、その日の最新版が入る。
    テストした版（uv.lock）と配布物の版がずれる。実測: pypdf は
    uv.lock 6.16.1 / Mac 6.18.1 / Windows 6.19.0 だった。
    Flet は package ステップを build/.hash/package で使い回すので、
    ローカルでは「古い版が残る」こともある。

やること:
    1. `uv export` で uv.lock の全パッケージ（推移的依存を含む）を == 固定で書き出す
    2. pyproject.toml の dependencies を、その固定リストに一時的に差し替える
       （Flet は [project].dependencies があればそれを使い、requirements.txt は読まない）
    3. 渡されたコマンド（flet build ...）を実行する
    4. 成功・失敗にかかわらず pyproject.toml を元に戻す
    固定リストは文字列が変わるので、Flet の package キャッシュも自然に作り直される。

使い方:
    uv run python scripts/pinned_build.py build -- flet build macos . ...
    uv run python scripts/pinned_build.py verify     # build/site-packages が uv.lock と一致するか
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
# macOS は build/site-packages/<arch>/ の下、Windows は直下に展開される。
SITE_PACKAGES = ROOT / "build" / "site-packages"
# 行頭の `dependencies = [` から、行頭の `]` まで（[project] の実行時依存）。
DEPS_BLOCK = re.compile(r"^dependencies = \[\n.*?^\]\n", re.DOTALL | re.MULTILINE)


def locked_pins() -> list[str]:
    """uv.lock の実行時依存を `名前==版 ; マーカー` の行で返す（dev グループは除く）。"""
    out = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-hashes", "--no-emit-project", "--no-header"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build(cmd: list[str]) -> int:
    original = PYPROJECT.read_bytes()
    text = original.decode("utf-8")
    pins = locked_pins()
    block = "dependencies = [\n" + "".join(f'    "{p}",\n' for p in pins) + "]\n"
    new_text, n = DEPS_BLOCK.subn(lambda _m: block, text, count=1)
    if n != 1:
        print("pyproject.toml の dependencies が見つかりません", file=sys.stderr)
        return 2
    print(f"[pinned_build] uv.lock から {len(pins)} 件を固定して実行: {' '.join(cmd)}")
    PYPROJECT.write_bytes(new_text.encode("utf-8"))
    try:
        return subprocess.run(["uv", "run", "--frozen", *cmd], cwd=ROOT).returncode
    finally:
        PYPROJECT.write_bytes(original)


def verify() -> int:
    """ビルドが同梱した版が uv.lock と食い違っていないかを調べる。"""
    pins = {}
    for ln in locked_pins():
        name, _, rest = ln.partition("==")
        pins[normalize(name)] = rest.split(";")[0].strip()
    found = {}
    for info in SITE_PACKAGES.glob("**/*.dist-info"):
        name, _, ver = info.name.removesuffix(".dist-info").rpartition("-")
        found[normalize(name)] = ver
    if not found:
        print(f"{SITE_PACKAGES} に dist-info がありません。先にビルドしてください", file=sys.stderr)
        return 2
    bad = [
        f"{n}: 同梱 {v} / uv.lock {pins.get(n, '(無い)')}"
        for n, v in sorted(found.items())
        if pins.get(n) != v
    ]
    # pip / setuptools など、ビルド道具が混ざるものは対象外。
    bad = [b for b in bad if b.split(":")[0] not in {"pip", "setuptools", "wheel"}]
    if bad:
        print("uv.lock と食い違う同梱パッケージ:\n  " + "\n  ".join(bad), file=sys.stderr)
        return 1
    print(f"[verify] 同梱 {len(found)} 件、すべて uv.lock と一致")
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "verify":
        return verify()
    if len(sys.argv) >= 4 and sys.argv[1] == "build" and sys.argv[2] == "--":
        return build(sys.argv[3:])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
