"""Flet のビルド雛形を取り出し、macOS の最小 OS を 12.0 に上げて置く。

使い方:
    uv run python scripts/patch_flet_template.py   # 直した雛形のパスを 1 行出す

flet build macos --template <出力されたパス> で使う（scripts/build.zsh が呼ぶ）。

なぜ要るか:
    Flet 0.86.2 の雛形は MACOSX_DEPLOYMENT_TARGET を 11.0 にしており、
    Flutter の FlutterMacOS pod は 10.15 を宣言している。Xcode 27 は 12.0 未満を
    受け付けず、flet build が「range of supported deployment target versions is
    12.0 to 27.0.x」で落ちる。build/flutter を手で直しても、Flet が雛形から
    作り直すと消えるので、雛形の側を直す。

雛形の書き方が変わって置き換えが当たらなくなったら、黙って進めずに落とす。
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

import flet.version
from flet_cli.commands.build_base import DEFAULT_TEMPLATE_URL
from flet_cli.utils.template_cache import get_cached_template_zip

MIN_MACOS = "12.0"
ROOT = Path(__file__).resolve().parents[1]

# (ファイル, 置き換え前, 置き換え後, 当たるはずの回数)
PATCHES = [
    (
        "macos/Podfile",
        "platform :osx, '11.0'",
        f"platform :osx, '{MIN_MACOS}'",
        1,
    ),
    (
        "macos/Podfile",
        # FlutterMacOS pod は podspec の 10.15 のまま入るので、pod 側も揃える
        "    flutter_additional_macos_build_settings(target)\n",
        "    flutter_additional_macos_build_settings(target)\n"
        "    target.build_configurations.each do |config|\n"
        f"      config.build_settings['MACOSX_DEPLOYMENT_TARGET'] = '{MIN_MACOS}'\n"
        "    end\n",
        1,
    ),
    (
        "macos/Runner.xcodeproj/project.pbxproj",
        "MACOSX_DEPLOYMENT_TARGET = 11.0;",
        f"MACOSX_DEPLOYMENT_TARGET = {MIN_MACOS};",
        3,
    ),
]


def main() -> int:
    version = flet.version.flet_version
    src = get_cached_template_zip(DEFAULT_TEMPLATE_URL.format(version=version), version)
    dest = ROOT / "build" / "flet-template" / f"v{version}-macos{MIN_MACOS}"

    shutil.rmtree(dest, ignore_errors=True)
    with zipfile.ZipFile(src) as z:
        z.extractall(dest)

    # zip の中は build/cookiecutter.json と build/{{cookiecutter.out_dir}}/...
    template = dest / "build"
    out_dirs = list(template.glob("{{cookiecutter.out_dir}}"))
    if not (template / "cookiecutter.json").is_file() or len(out_dirs) != 1:
        print(f"雛形の構成が想定と違います: {src}", file=sys.stderr)
        return 1

    for rel, old, new, expected in PATCHES:
        path = out_dirs[0] / rel
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != expected:
            print(
                f"{rel}: 置き換え対象が {expected} 箇所のはずが {found} 箇所でした"
                f"（Flet {version} の雛形が変わった可能性）: {old!r}",
                file=sys.stderr,
            )
            return 1
        path.write_text(text.replace(old, new), encoding="utf-8")

    print(template)
    return 0


if __name__ == "__main__":
    sys.exit(main())
