"""アプリのエントリポイント。

flet build はこのファイルを起点にパッケージする。実装は
src/archival_packager/ui/app.py にあり、ここは薄い入口だけにしておく
（パッケージ後も同じコードが動くことを保ちやすい）。
"""

import sys
from pathlib import Path

# パッケージ後は sys.path にソースが載らないため、ここで通す。
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import os  # noqa: E402

from archival_packager.core import applog  # noqa: E402
from archival_packager.ui.app import SELF_TEST_ENV, run  # noqa: E402

# 起動したことを記録に残す。包んだアプリは標準出力が呼び出し元に戻らないため、
# 「そもそも入口まで来ているか」をここでしか確かめられない。
applog.record(
    "起動",
    f"argv={sys.argv} {SELF_TEST_ENV}={os.environ.get(SELF_TEST_ENV, '')!r}",
)

if __name__ == "__main__":
    run()
