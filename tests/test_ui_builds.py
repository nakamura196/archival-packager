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
