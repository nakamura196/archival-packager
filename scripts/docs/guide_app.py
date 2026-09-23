"""ガイドの動画を録るために、アプリをブラウザ向けに起動する。

`record_guide.py` から子プロセスとして呼ばれる。単独では使わない。

## 実物との違い（動画で隠していること）

**フォルダを選ぶ窓は出ない。** ブラウザ版の Flet はフォルダ選択に対応して
いないので、`FilePicker.get_directory_path` を差し替え、環境変数
`GUIDE_PICKS`（`|` 区切り）の順に返す。ガイドの本文には「フォルダを選ぶ
窓が開きます」と書いて補う。

**言語の切り替えを設定ファイルに書かない。** 実物は `set_language` が
settings.json に保存する。録画でそれをやると、開発者の手元のアプリの
言語が録画のたびに変わる。保存先を一時ディレクトリへ向ける。

それ以外（画面・処理・出力）は実物のまま走る。`core/` にも `ui/` にも
録画用の分岐は入れない。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import flet as ft

from archival_packager import i18n

_settings = Path(tempfile.mkdtemp(prefix="guide-settings-")) / "settings.json"
i18n._settings_path = lambda: _settings  # type: ignore[assignment]
i18n._current = os.environ.get("GUIDE_LANG", "ja")

from archival_packager.ui import app  # noqa: E402  言語を決めてから画面を読む

_picks = [p for p in os.environ.get("GUIDE_PICKS", "").split("|") if p]


async def _pick_directory(self, *args, **kwargs) -> str | None:
    return _picks.pop(0) if _picks else None


ft.FilePicker.get_directory_path = _pick_directory  # type: ignore[method-assign]

if __name__ == "__main__":
    os.environ["FLET_FORCE_WEB_SERVER"] = "1"
    ft.run(app.main, port=int(os.environ.get("GUIDE_PORT", "8551")))
