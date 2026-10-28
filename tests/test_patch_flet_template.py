"""scripts/patch_flet_template.py が、いまの Flet の雛形に当たるかを確かめる。

Xcode 27.1 で flet build macos が「deployment target 11.0 は範囲外（12.0〜）」で
落ちた（2026-10-27）。雛形を書き換えて回避しているが、Flet の版を上げて
雛形の書き方が変わると、書き換えが当たらずにまた落ちる。それをビルドより先に
（ふつうのテストで）知るためのテスト。

雛形の zip は flet build の初回に取得される。手元に無い環境（Linux の CI など）
では取得しにいかずに飛ばす。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import flet.version
import pytest
from flet_cli.utils.template_cache import get_cache_root

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "patch_flet_template.py"
CACHED_ZIP = (
    get_cache_root()
    / "build-template"
    / f"v{flet.version.flet_version}"
    / "flet-build-template.zip"
)


def _load():
    spec = importlib.util.spec_from_file_location("patch_flet_template", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not CACHED_ZIP.is_file(), reason="Flet の雛形が未取得")
def test_patch_raises_macos_minimum_to_12(capsys):
    """書き換えが全部当たり、11.0 / 10.15 が残らないこと。"""
    module = _load()
    assert module.main() == 0, capsys.readouterr().err

    template = Path(capsys.readouterr().out.strip())
    macos = next(template.glob("{{cookiecutter.out_dir}}")) / "macos"
    podfile = (macos / "Podfile").read_text(encoding="utf-8")
    pbxproj = (macos / "Runner.xcodeproj" / "project.pbxproj").read_text(
        encoding="utf-8"
    )

    assert "platform :osx, '12.0'" in podfile
    # FlutterMacOS pod の 10.15 は podspec 由来なので、post_install で上書きする
    assert "config.build_settings['MACOSX_DEPLOYMENT_TARGET'] = '12.0'" in podfile
    assert pbxproj.count("MACOSX_DEPLOYMENT_TARGET = 12.0;") == 3
    assert "MACOSX_DEPLOYMENT_TARGET = 11.0;" not in pbxproj


def test_patch_fails_loudly_when_template_changes(tmp_path, monkeypatch, capsys):
    """置き換え対象が見つからないときは、黙って進めず 1 を返すこと。"""
    import zipfile

    fake = tmp_path / "flet-build-template.zip"
    with zipfile.ZipFile(fake, "w") as z:
        z.writestr("build/cookiecutter.json", "{}")
        z.writestr("build/{{cookiecutter.out_dir}}/macos/Podfile", "platform :osx, '13.0'\n")
        z.writestr(
            "build/{{cookiecutter.out_dir}}/macos/Runner.xcodeproj/project.pbxproj", ""
        )

    module = _load()
    monkeypatch.setattr(module, "get_cached_template_zip", lambda *_: fake)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    assert module.main() == 1
    assert "置き換え対象" in capsys.readouterr().err
