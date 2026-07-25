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

from archival_packager.ui.app import run  # noqa: E402

if __name__ == "__main__":
    run()
