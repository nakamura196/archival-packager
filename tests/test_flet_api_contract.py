"""UI が呼んでいる Flet の名前が、実際に入っている Flet に在るかを確かめる。

なぜ要るか
----------
0.1.4 で `ft.ExpansionTile(initially_expanded=...)` を書いて配ってしまった。
Flet 0.86 にその引数は無く、アプリは起動直後に落ちた。
それでも次のどれにも引っかからなかった。

  - 単体テスト: UI をその場で組み立てるテストが無い
  - CI の起動確認: 窓は出る（中身が Flet のエラー画面になるだけ）
  - 型チェック: Flet の Control は動的な属性を許す

そこで、UI のソースを読んで「ft.〜」の名前と引数を全部拾い、
入っている Flet に実在するかを機械的に突き合わせる。
実行に画面は要らないので、macOS でも Windows でも同じように効く。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import flet as ft
import pytest

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "archival_packager" / "ui"
UI_FILES = sorted(UI_DIR.glob("*.py"))


def _attr_chain(node: ast.AST) -> list[str] | None:
    """`ft.Colors.ON_SURFACE_VARIANT` のような並びを ['Colors', ...] にする。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and node.id == "ft":
        return list(reversed(parts))
    return None


def _accepted_names(cls) -> set[str] | None:
    """そのクラスが受け取れるキーワード。分からなければ None（＝調べない）。"""
    if dataclasses.is_dataclass(cls):
        return {f.name for f in dataclasses.fields(cls)}
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return None
    if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
        return None
    return {n for n in sig.parameters if n != "self"}


def _iter_ft_calls():
    for path in UI_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                chain = _attr_chain(node.func)
                if chain:
                    yield path, node, chain


@pytest.mark.parametrize("path", UI_FILES, ids=lambda p: p.name)
def test_referenced_names_exist(path: Path):
    """ft.〜 で触っている名前が、入っている Flet に在ること。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain = _attr_chain(node)
        if not chain:
            continue
        obj = ft
        for i, name in enumerate(chain):
            if not hasattr(obj, name):
                missing.append(f"{path.name}:{node.lineno} ft.{'.'.join(chain[: i + 1])}")
                break
            obj = getattr(obj, name)
    assert not missing, "Flet に無い名前を使っています:\n  " + "\n  ".join(sorted(set(missing)))


def test_keyword_arguments_exist():
    """ft.〜(...) に渡しているキーワードが、そのクラスに在ること。

    0.1.4 の `initially_expanded` はここで落ちる。
    """
    bad: list[str] = []
    for path, node, chain in _iter_ft_calls():
        obj = ft
        for name in chain:
            if not hasattr(obj, name):
                obj = None
                break
            obj = getattr(obj, name)
        if not inspect.isclass(obj):
            continue
        accepted = _accepted_names(obj)
        if accepted is None:
            continue
        for kw in node.keywords:
            if kw.arg is None:  # **kwargs
                continue
            if kw.arg not in accepted:
                bad.append(f"{path.name}:{node.lineno} ft.{'.'.join(chain)}({kw.arg}=…)")
    assert not bad, "そのクラスに無い引数を渡しています:\n  " + "\n  ".join(sorted(set(bad)))
