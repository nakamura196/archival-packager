"""Archivematica の FPR の「形式の UUID → PRONOM の PUID」の対応表を作り直す。

    uv run python scripts/gen_fpr_formats.py <Archivematica の checkout>

出力は src/archival_packager/core/fpr_formats.py。

## 何を写し、何を写さないか

写すのは **識別子の対（uuid と pronom_id）だけ**。説明文・名前などの文章は写さない。
AP は MIT、Archivematica は AGPL-3.0 なので、文章を持ち込むとライセンスの扱いが
面倒になる。識別子（UUID と PRONOM の PUID）は、利用者が書いた規則ファイルを
読むために要る事実であって、文章ではない。

## なぜ Python の定数にするか

JSON のデータファイルにすると、配布物（PyInstaller / MSIX）の設定に 1 行足さないと
**配布版だけ対応表が空になる**。rule_table.BUILTIN_TOML と同じ理由で、import できた
時点で必ず在る形にしてある。
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

INITIAL_DATA = "src/archivematica/dashboard/fpr/migrations/initial_data.json"
OUT = Path(__file__).resolve().parent.parent / "src" / "archival_packager" / "core" / "fpr_formats.py"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    root = Path(argv[1]).expanduser().resolve()
    data = json.loads((root / INITIAL_DATA).read_text(encoding="utf-8"))
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip() or "unknown"

    pairs = {
        rec["fields"]["uuid"]: rec["fields"]["pronom_id"]
        for rec in data
        if rec.get("model") == "fpr.formatversion"
        and rec["fields"].get("pronom_id")
        and rec["fields"].get("uuid")
    }

    lines = [
        '"""Archivematica の FPR の形式 UUID から PRONOM の PUID への対応表（自動生成）。',
        "",
        "scripts/gen_fpr_formats.py が作る。**手で直さない。** 写しているのは識別子の対だけで、",
        "文章は含まない（理由は生成スクリプトの冒頭）。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "SOURCE = (",
        f'    "archivematica/archivematica {INITIAL_DATA}"',
        f'    " @ {commit}"',
        ")",
        f'GENERATED = "{datetime.now(UTC).date().isoformat()}"',
        "",
        "FORMATS: dict[str, str] = {",
    ]
    lines += [f'    "{u}": "{p}",' for u, p in sorted(pairs.items(), key=lambda kv: (kv[1], kv[0]))]
    lines += ["}", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(pairs)} 件を {OUT} に書きました（{commit[:12]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
