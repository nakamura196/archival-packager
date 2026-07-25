#!/usr/bin/env python3
"""現行 Swift 実装との出力差分検証。

同じ入力から現行 aip（Swift）と新実装で SIP/AIP を生成し、生成時刻や UUID など
本質的に変わる情報を除いて一致するかを確かめる。移植の検証としては、
個々の単体テストよりこれが効く。単体テストは「自分が想定した仕様」を固定するが、
これは「現行実装が実際にやっていること」と突き合わせる。

## 使い方

    uv run python scripts/differential_check.py --swift-app /path/to/Archival\\ Packager.app

現行実装には --headless sip|aip|full があるのでスクリプトから駆動できる。
.app が無い場合は SWIFT_APP 環境変数、または --swift-app で指定する。

## 何を比較し、何を除外するか

比較する:
  - objects/ 配下のファイル一覧と各ファイルの SHA-256（ペイロードの同一性）
  - checksum.sha256 / manifest-sha256.txt の内容
  - CSV 各種の見出しと行（description / formats / accession / metadata）
  - METS の構造（要素の親子関係と主要な値）

除外する（本質的に一致しえないもの）:
  - 生成日時（report の生成日時、bag-info.txt の Bagging-Date、METS の CREATEDATE）
  - UUID（AIP UUID、PREMIS の object/event 識別子、METS の ID 属性）
  - Bag-Software-Agent（bagit.py と自前実装で必ず異なる）
  - Payload-Oxum 以外の bag-info 生成系項目

除外した項目は「一致しないことが分かっている」ものだけに限る。判断に迷うものは
除外せず、差分として出して人が見る。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from archival_packager.core import sip_pipeline  # noqa: E402
from archival_packager.core.models import SIPMetadata, SIPOptions  # noqa: E402

# 生成のたびに変わる値。比較前に伏せる。
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# bag-info.txt のうち、実装が違えば必ず変わる行。
_VOLATILE_BAG_INFO = ("Bagging-Date", "Bag-Software-Agent")

# 比較対象から外すファイル。中身が生成時刻や実装名で埋まっているもの。
_SKIP_FILES = frozenset({"report.txt", "report.html", "dfxml.xml"})


@dataclass
class Diff:
    kind: str
    path: str
    detail: str = ""


@dataclass
class Report:
    compared: int = 0
    diffs: list[Diff] = field(default_factory=list)

    def add(self, kind: str, path: str, detail: str = "") -> None:
        self.diffs.append(Diff(kind, path, detail))

    @property
    def ok(self) -> bool:
        return not self.diffs


def normalize(text: str) -> str:
    """生成のたびに変わる値を伏せる。"""
    text = _UUID_RE.sub("<UUID>", text)
    text = _ISO_RE.sub("<TIMESTAMP>", text)
    text = _DATE_RE.sub("<DATE>", text)
    return text


def normalize_bag_info(text: str) -> str:
    kept = [
        line
        for line in text.splitlines()
        if not any(line.startswith(f"{k}:") for k in _VOLATILE_BAG_INFO)
    ]
    return normalize("\n".join(sorted(kept)))


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def make_sample_input(root: Path) -> Path:
    """両実装に同じものを食わせる入力ツリー。

    日本語名・空白・入れ子・拡張子と中身の食い違い・空ファイルを含める。
    「普通のファイルだけ一致した」では移植の検証にならない。
    """
    src = root / "input"
    (src / "文書 2024" / "sub").mkdir(parents=True)

    (src / "a.txt").write_text("資料 A の本文\n", encoding="utf-8")
    (src / "文書 2024" / "報告書.txt").write_text("報告書の本文\n", encoding="utf-8")
    (src / "文書 2024" / "sub" / "empty.txt").write_bytes(b"")
    # 拡張子と中身が食い違うもの（warning が立つ）。
    (src / "文書 2024" / "sub" / "mislabeled.txt").write_bytes(b"%PDF-1.4\ntrailer\n%%EOF\n")
    # 既に PDF のもの。
    (src / "genuine.pdf").write_bytes(b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF\n")

    return src


def run_swift(app: Path, mode: str, input_path: Path, out: Path) -> Path:
    """現行 Swift 実装を --headless で走らせ、生成物のディレクトリを返す。"""
    exe = next((app / "Contents" / "MacOS").iterdir())
    out.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        [str(exe), "--headless", mode, str(input_path), str(out),
         "--identifier", "差分検証", "--title", "差分検証", "--no-bag"],
        capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"Swift 版の実行に失敗しました (code {proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )

    produced = [p for p in out.iterdir() if p.is_dir()]
    if len(produced) != 1:
        raise SystemExit(f"Swift 版の出力を特定できません: {produced}")
    return produced[0]


def run_python(input_path: Path, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    result = sip_pipeline.run(
        input_path=input_path,
        output_parent=out,
        metadata=SIPMetadata(identifier="差分検証", title="差分検証"),
        options=SIPOptions(make_bag=False),
        progress=lambda _m: None,
    )
    return result.sip_path


def compare_trees(swift: Path, python: Path, report: Report) -> None:
    """2 つの成果物を突き合わせる。"""

    def listing(root: Path) -> dict[str, Path]:
        return {
            p.relative_to(root).as_posix(): p
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name not in {".DS_Store"}
        }

    a, b = listing(swift), listing(python)

    for rel in sorted(set(a) - set(b)):
        report.add("Python 側に無い", rel)
    for rel in sorted(set(b) - set(a)):
        report.add("Swift 側に無い", rel)

    for rel in sorted(set(a) & set(b)):
        name = Path(rel).name
        if name in _SKIP_FILES:
            continue

        report.compared += 1

        if rel.startswith("objects/"):
            # ペイロードはバイト単位で一致すべき。
            if digest(a[rel]) != digest(b[rel]):
                report.add("ペイロード不一致", rel)
            continue

        if name == "bag-info.txt":
            if normalize_bag_info(_text(a[rel])) != normalize_bag_info(_text(b[rel])):
                report.add("内容不一致", rel, _first_difference(_text(a[rel]), _text(b[rel])))
            continue

        left, right = normalize(_text(a[rel])), normalize(_text(b[rel]))
        if left != right:
            report.add("内容不一致", rel, _first_difference(left, right))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").lstrip("﻿")


def _first_difference(left: str, right: str) -> str:
    """最初に食い違う箇所を返す。

    行を丸ごと切り詰めて出すと、片方だけ長いときに「列数が違う」ように見えて
    誤診する（実際に一度そうなった）。CSV は列単位で、それ以外は
    食い違い位置の周辺だけを出す。
    """
    la, lb = left.splitlines(), right.splitlines()
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x == y:
            continue
        fa, fb = next(csv.reader([x]), []), next(csv.reader([y]), [])
        if len(fa) > 1 or len(fb) > 1:
            if len(fa) != len(fb):
                return f"{i} 行目: 列数が違う（Swift {len(fa)} / Python {len(fb)}）"
            for col, (u, v) in enumerate(zip(fa, fb)):
                if u != v:
                    return f"{i} 行目 {col + 1} 列目\n    Swift : {u}\n    Python: {v}"
        at = next((k for k, (u, v) in enumerate(zip(x, y)) if u != v), min(len(x), len(y)))
        lo = max(0, at - 40)
        return f"{i} 行目 {at + 1} 文字目付近\n    Swift : {x[lo:at + 60]}\n    Python: {y[lo:at + 60]}"
    if len(la) != len(lb):
        return f"行数が違う: Swift {len(la)} / Python {len(lb)}"
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="現行 Swift 実装との出力差分検証")
    parser.add_argument(
        "--swift-app",
        type=Path,
        default=Path(os.environ["SWIFT_APP"]) if os.environ.get("SWIFT_APP") else None,
        help="現行実装の .app（--headless で駆動する）",
    )
    parser.add_argument("--keep", action="store_true", help="作業ディレクトリを残す")
    args = parser.parse_args()

    if args.swift_app is None or not args.swift_app.exists():
        print(
            "現行実装の .app を指定してください（--swift-app または SWIFT_APP）。\n"
            "  例: uv run python scripts/differential_check.py \\\n"
            "        --swift-app ~/git/kim/aip/app/build/dd_rel/.../Archival\\ Packager.app",
            file=sys.stderr,
        )
        return 2

    work = Path(tempfile.mkdtemp(prefix="differential-check-"))
    try:
        src = make_sample_input(work)
        swift_out = run_swift(args.swift_app, "sip", src, work / "swift")
        python_out = run_python(src, work / "python")

        report = Report()
        compare_trees(swift_out, python_out, report)

        print(f"比較したファイル: {report.compared} 件")
        if report.ok:
            print("差分なし。移植後の出力は現行実装と一致しています。")
            return 0

        print(f"\n差分 {len(report.diffs)} 件:\n")
        for d in report.diffs:
            print(f"  [{d.kind}] {d.path}")
            if d.detail:
                for line in d.detail.splitlines():
                    print(f"      {line}")
        print(
            "\n注: 生成日時・UUID・Bag-Software-Agent は比較前に伏せています。"
            "\n    ここに出た差分は、移植の齟齬か、意図的な仕様変更のどちらかです。"
        )
        return 1

    finally:
        if args.keep:
            print(f"\n作業ディレクトリ: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
