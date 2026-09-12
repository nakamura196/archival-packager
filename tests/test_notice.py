"""NOTICE が、実際に配布するものと一致していること。

なぜこのテストがあるか
----------------------
2026-09-12、配布物の .app には Python のパッケージが 21 件入っていたのに、
NOTICE には主要な 7 件しか書いていなかった。表示の義務がある certifi（MPL-2.0）が
記載漏れになっていた。さらにその前は `flet[all]` を指定していたため、
ビルド用ツールの依存（chardet は LGPL-2.1+、text-unidecode は Artistic/GPL）まで
配布物に入ったまま、NOTICE には一言も書かれていなかった。

**「依存に何を書いたか」と「実際に何を配るか」は別物である。**
依存を足し引きすれば配布物は変わるが、NOTICE は誰かが直すまで変わらない。
ここで機械的に突き合わせておかないと、また静かにずれる。

実行時依存はロックファイルから取る（配布物のビルドを要求すると、
テストがビルド環境に依存してしまうため）。
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NOTICE = ROOT / "NOTICE"


def _runtime_packages() -> set[str]:
    """配布物に入る実行時依存の名前。dev グループは除く。"""
    out = subprocess.run(
        ["uv", "export", "--no-dev", "--no-hashes", "--no-emit-project",
         "--format", "requirements-txt"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    if out.returncode != 0:
        pytest.skip(f"uv export を実行できません: {out.stderr[:200]}")
    names = set()
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = re.split(r"[=<>;\[ ]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


def _notice_text() -> str:
    return NOTICE.read_text(encoding="utf-8").lower().replace("_", "-")


class TestNoticeCoversWhatWeShip:
    def test_every_runtime_package_is_listed(self):
        listed = _notice_text()
        missing = sorted(p for p in _runtime_packages() if p not in listed)
        assert not missing, (
            "配布物に入るのに NOTICE に無いパッケージ: " + ", ".join(missing)
            + "。NOTICE の「Python 実行環境とライブラリ」に足すこと。"
        )

    def test_build_only_tools_are_not_declared_as_bundled(self):
        """ビルド用ツールを実行時依存に戻したら気づけるようにする。

        flet-cli を実行時依存にすると、その依存（cookiecutter / fastapi /
        chardet / text-unidecode …）がまとめて配布物に入る。
        """
        runtime = _runtime_packages()
        leaked = sorted(runtime & {"flet-cli", "flet-web", "cookiecutter",
                                   "chardet", "text-unidecode", "fastapi",
                                   "uvicorn", "starlette", "pytest"})
        assert not leaked, (
            "ビルド用・開発用のパッケージが実行時依存に入っています: " + ", ".join(leaked)
            + "。pyproject.toml の dependencies を確認すること（flet[all] は使わない）。"
        )

    def test_copyleft_components_are_called_out(self):
        """表示だけで済まないものは、本文で名指しされていること。"""
        text = NOTICE.read_text(encoding="utf-8")
        assert "GPL-2.0" in text and "clamav" in text.lower(), "ClamAV の GPL-2.0 の記載が無い"
        assert "ソースコードの入手手段" in text, "GPL のソース入手手段の記載が無い"
        assert "MPL-2.0" in text, "certifi の MPL-2.0 の記載が無い"

    def test_licence_of_the_application_itself(self):
        text = NOTICE.read_text(encoding="utf-8")
        assert "MIT" in text
        assert (ROOT / "LICENSE").is_file()


class TestPyprojectDoesNotUseFletAll:
    """`flet[all]` は 105MB 分の不要物と、表示していないコピーレフトを連れてくる。"""

    def test_dependencies_do_not_ask_for_all(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        deps = " ".join(data["project"]["dependencies"])
        assert "flet[all]" not in deps.replace(" ", ""), (
            "flet[all] はビルド用ツールまで配布物に入れてしまう。flet[desktop] を使うこと。"
        )
