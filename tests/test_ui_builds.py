"""画面を最後まで組み立てられることを確かめる。

なぜ要るか
----------
0.1.4 は起動した瞬間に落ちた。原因は 2 つとも「画面を組み立てる途中」にあった。

  1. ft.ExpansionTile に、Flet 0.86 には無い引数を渡していた
  2. まだ画面に載っていないコントロールの .page を読んでいた
     （Flet 0.86 では None ではなく RuntimeError が返る）

どちらも、出力の正しさを見るテストでは触れない場所だった。
CI の起動確認も「窓が出たか」しか見ておらず、窓の中が Flet の
エラー画面になっていても通ってしまう。

ここでは偽の Page を渡して main() を最後まで走らせる。画面の描画は要らないので、
macOS でも Windows でも Linux でも同じように動く。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from archival_packager.ui import about, app


def test_main_builds_the_whole_screen():
    page = MagicMock()
    app.main(page)
    assert page.add.call_count == 1, "画面を page に載せていない"


def test_about_screen_builds():
    control = about.build(on_close=lambda: None)
    assert control is not None


def test_main_does_not_touch_page_before_adding():
    """page.add より前に .update() を呼んでいないこと。

    呼ぶと Flet が「まだ画面に載っていない」と言って落ちる。
    順番を入れ替えたときに気づけるよう、ここで押さえる。
    """
    page = MagicMock()
    calls: list[str] = []
    page.add.side_effect = lambda *_a, **_k: calls.append("add")
    page.update.side_effect = lambda *_a, **_k: calls.append("update")
    app.main(page)
    assert "add" in calls
    assert calls.index("add") == 0 or "update" not in calls[: calls.index("add")]


class TestRunButtonBecomesPressable:
    """実行ボタンが押せるようになるまでを、実際に画面を動かして確かめる。

    「実行ボタンを押せない」という報告を受けた。仕様どおり（素材フォルダ・
    出力先・タイトルが揃うまで押せない）だったが、**なぜ押せないのかが
    画面に出ていなかった**。仕様が変わったときに気づけるよう、
    ここで順序ごと押さえる。
    """

    def _screen(self):
        import flet as ft

        page = MagicMock()
        app.main(page)

        picker = next(
            c[0][0]
            for c in page.services.append.call_args_list
            if isinstance(c[0][0], ft.FilePicker)
        )

        found: list = []

        def walk(control):
            found.append(control)
            for attr in ("controls", "content", "title", "subtitle", "leading", "trailing"):
                value = getattr(control, attr, None)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, ft.Control):
                            walk(item)
                elif isinstance(value, ft.Control):
                    walk(value)

        walk(page.add.call_args[0][0])
        return picker, found

    def test_pressable_only_after_everything_is_chosen(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock

        import flet as ft

        picker, found = self._screen()
        run = next(c for c in found if getattr(c, "content", None) == "実行")
        choosers = [c for c in found if getattr(c, "content", None) == "フォルダを選ぶ"]
        title = next(
            c
            for c in found
            if isinstance(c, ft.TextField) and c.label and "タイトル" in c.label
        )

        assert run.disabled, "何も選んでいないのに押せる"

        picker.get_directory_path = AsyncMock(return_value=str(tmp_path))
        for chooser in choosers:
            asyncio.run(chooser.on_click(MagicMock()))
        assert run.disabled, "タイトルが空でも押せてしまう"

        title.value = "テスト資料"
        title.on_change(MagicMock())
        assert not run.disabled, "すべて埋めても押せない"

    def test_options_are_above_the_descriptive_metadata(self):
        """「オプション」を「記述メタデータ」より上に置くこと。

        既定のウィンドウ（1000x880）では、左の列は「担当者名」のあたりで
        切れる。記述メタデータは 5 欄あって縦に長く、その下にオプションを
        置くと**画面に出ない**。個人情報の走査もウイルス検査も、スクロール
        しないと存在に気づけない状態になっていた。

        高さを 880 に上げてこれを直した記録が app.py に残っているが、
        そのあと実行ボタンを下に固定した改修が入り、その高さぶんだけ
        押し戻されて元に戻っていた。**前の修正を後の修正が打ち消した。**
        並び順で押さえておけば、高さの取り合いで再発しない。
        """
        import flet as ft

        _, found = self._screen()
        headings = [
            i for i, c in enumerate(found)
            if isinstance(c, ft.Text) and c.value in ("オプション", "記述メタデータ")
        ]
        labels = [found[i].value for i in headings]
        assert labels == ["オプション", "記述メタデータ"], labels

    def test_says_what_is_missing(self, tmp_path):
        """押せない理由を画面に出すこと。灰色のボタンだけでは伝わらない。"""
        import asyncio
        from unittest.mock import AsyncMock

        import flet as ft

        picker, found = self._screen()
        hints = [
            c.value
            for c in found
            if isinstance(c, ft.Text) and c.value and "押せます" in c.value
        ]
        assert hints, "何が足りないのかを書いていない"
        assert "タイトル" in hints[0] and "出力先" in hints[0]

        picker.get_directory_path = AsyncMock(return_value=str(tmp_path))
        for chooser in [c for c in found if getattr(c, "content", None) == "フォルダを選ぶ"]:
            asyncio.run(chooser.on_click(MagicMock()))
        title = next(
            c for c in found
            if isinstance(c, ft.TextField) and c.label and "タイトル" in c.label
        )
        title.value = "テスト資料"
        title.on_change(MagicMock())

        remaining = [
            c.value
            for c in found
            if isinstance(c, ft.Text) and c.value and "押せます" in c.value
        ]
        assert not remaining, f"すべて埋めたのに案内が残っている: {remaining}"
