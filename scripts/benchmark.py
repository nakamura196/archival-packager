#!/usr/bin/env python3
"""SIP / AIP パイプラインの規模限界を実測する。

文書館の移管は数万ファイルになることがあるのに、このアプリは
「何ファイル・何 GB まで実用か」を一度も測っていなかった。
破綻する規模を先に知るための道具。結果は docs/performance.md に残す。

## 設計の理由

**`core/` には一切手を入れない。** 計測用のフックをコードに埋めると、
計測をやめた後も残って本番経路を汚す。代わりに、このスクリプトが
実行時にモジュール属性を差し替えて各段を包む（`_instrument`）。
包むのは呼び出しの入口だけなので、パイプライン本体は素のまま走る。

**1 規模 = 1 子プロセス。** ピークメモリは `resource.getrusage` の
`ru_maxrss` で測るが、これはプロセスの生涯最大値なので、同じプロセスで
100 件と 50,000 件を続けて測ると前者が後者の値に汚染される。
子プロセスに分ければ、その規模だけの最大値が取れる。おまけに
`RUSAGE_CHILDREN` で siegfried / clamscan 側のメモリも別枠で拾える。

**tracemalloc は既定で無効。** Python の割り当てを全部追うので
実時間が目に見えて延び、同じ実行で時間とメモリの両方を測ると
時間のほうが信用できなくなる。`--tracemalloc` で別途走らせる。
なお lxml の木は C 側で確保されるため tracemalloc には映らない。
そちらは ru_maxrss でしか見えない。

## 使い方

    uv run python scripts/benchmark.py --suite quick    # 数分
    uv run python scripts/benchmark.py --suite full     # 50,000 件・1GB を含む
    uv run python scripts/benchmark.py --case files:1000 --virus
    uv run python scripts/benchmark.py --suite quick --format json

作業用データは一時ディレクトリに作り、終わったら必ず消す
（`--keep` を付けたときだけ残す。置き場所は標準の一時領域で、
ホームディレクトリには何も書かない）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# 合成データ
# --------------------------------------------------------------------------

#: 実際の移管データに現れる名前の癖。ASCII だけで測ると、
#: 日本語名の NFD 分解・長い名前・記号のコストを見落とす。
#: 「移管データにはこれらが入る」という前提そのものを計測対象に含める。
_NAME_PATTERNS: tuple[str, ...] = (
    "plain_{i}.txt",
    "資料_{i}.txt",
    "議事録 平成30年度 {i}.txt",
    # 濁点・半濁点を含む語。macOS の readdir は分解形(NFD)で返すことがあり、
    # sanitize と NFC 検査のコストがここで効く。
    "ぱ行とが行の濁点入り資料_{i}.txt",
    "report(final)&draft[{i}].txt",
    # 長い名前。Windows の MAX_PATH 検査が効く長さにする。
    ("長" * 90) + "_{i}.txt",
    "2026-移管-総務課 — 第{i}号.txt",
)

#: 1 ディレクトリあたりのファイル数。実データも 1 か所に数万個は置かれない。
#: 全部を 1 ディレクトリに入れると readdir の特性だけを測ることになる。
_FILES_PER_DIR = 100


def _payload_for(index: int) -> bytes:
    """小さいテキストの中身。

    全ファイルを同一内容にしない。同一内容だと accession の突合が
    重複だらけになり、ファイルシステムのキャッシュも効きすぎて
    ハッシュ計算のコストが実態より軽く出る。
    """
    body = f"移管資料 {index} 行目\n" * 40
    return (f"# file {index}\n" + body).encode("utf-8")


def make_files_tree(root: Path, count: int) -> None:
    """小さいテキストを count 件、名前の癖を混ぜて作る。"""
    for i in range(count):
        sub = root / f"箱{i // _FILES_PER_DIR:04d}"
        sub.mkdir(parents=True, exist_ok=True)
        name = _NAME_PATTERNS[i % len(_NAME_PATTERNS)].format(i=i)
        (sub / name).write_bytes(_payload_for(i))


def make_bigfile_tree(root: Path, total_bytes: int, parts: int = 1) -> None:
    """大きいファイルを parts 個作る。

    中身は 1 MiB のブロックの繰り返し。0 埋めにするとファイルシステムが
    スパースに扱うことがあり、読み出しの実コストが測れない。
    """
    root.mkdir(parents=True, exist_ok=True)
    block = bytes((i * 31 + 7) % 251 for i in range(1 << 20))
    per_part = total_bytes // parts
    for p in range(parts):
        with (root / f"大容量資料_{p}.bin").open("wb") as fh:
            written = 0
            while written < per_part:
                chunk = block[: min(len(block), per_part - written)]
                fh.write(chunk)
                written += len(chunk)


def make_deep_tree(root: Path, count: int, depth: int) -> None:
    """深い階層に count 件を置く。相対パスの長さと mkdir 回数の効きを見る。"""
    base = root
    for d in range(depth):
        base = base / f"第{d + 1}階層_フォルダ"
    base.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        name = _NAME_PATTERNS[i % len(_NAME_PATTERNS)].format(i=i)
        (base / name).write_bytes(_payload_for(i))


# --------------------------------------------------------------------------
# 計測の道具
# --------------------------------------------------------------------------


@dataclass
class Timings:
    """段ごとの累計秒と、その段を抜けた時点でのピークメモリ。

    ピークは `ru_maxrss`（プロセス生涯の最高水位）なので、段ごとの
    「使用量」ではなく「ここまでで到達した最高水位」を意味する。
    値が跳ね上がった段が、メモリを押し上げた犯人である。
    段ごとの使用量を引き算で出さないのは、解放されたメモリが
    高水位に残り続けるため、引き算に意味が無いから。
    """

    stages: dict[str, float] = field(default_factory=dict)
    peak_after: dict[str, int] = field(default_factory=dict)

    def add(self, label: str, seconds: float, peak: int) -> None:
        self.stages[label] = self.stages.get(label, 0.0) + seconds
        self.peak_after[label] = max(self.peak_after.get(label, 0), peak)


def _wrap(owner: object, name: str, label: str, timings: Timings) -> None:
    """owner.name を「時間を測ってから元を呼ぶ」関数に差し替える。

    元の関数はクロージャに閉じ込めるだけで書き換えない。プロセスが
    終われば差し替えも消えるので、リポジトリのコードには何も残らない。
    """
    original = getattr(owner, name)

    def timed(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            timings.add(
                label, time.perf_counter() - started, _maxrss_bytes(resource.RUSAGE_SELF)
            )

    setattr(owner, name, timed)


@contextmanager
def _instrument(timings: Timings) -> Iterator[None]:
    """パイプラインの各段を包む。抜けるときに必ず元へ戻す。"""
    import bagit

    from archival_packager.core import (
        aip_pipeline,
        dfxml,
        fixity,
        mets,
        report,
        scan,
        sip_builder,
        sip_pipeline,
        sip_reader,
        spreadsheets,
    )

    # (対象モジュール, 属性名, 表示名)
    targets: list[tuple[object, str, str]] = [
        # SIP
        (scan, "scan", "走査"),
        (sip_pipeline, "_maybe_sanitize", "ファイル名安全化"),
        (sip_pipeline, "_identify_formats", "フォーマット識別"),
        (sip_pipeline, "_compute_checksums", "ハッシュ(SHA-256)"),
        (sip_pipeline, "_scan_virus", "ウイルス検査"),
        (sip_pipeline, "_scan_pii", "PII 走査"),
        (sip_pipeline, "_build_documents", "書類生成(合計)"),
        (spreadsheets, "description", "└ description.csv"),
        (spreadsheets, "formats", "└ formats.csv"),
        (spreadsheets, "accession", "└ accession.csv"),
        (spreadsheets, "metadata_template", "└ metadata.csv"),
        (dfxml, "build", "└ dfxml.xml"),
        (report, "text_report", "└ report.txt"),
        (report, "html_report", "└ report.html"),
        (sip_builder, "build", "SIP 組み立て(合計)"),
        (sip_builder, "_copy_payload", "└ ペイロード複写"),
        (sip_builder, "_write_submission_docs", "└ 書類書き出し"),
        (sip_builder, "_normalize_permissions", "└ 権限正規化"),
        (sip_builder, "collect_warnings", "└ 警告収集"),
        (sip_builder, "check_path_lengths", "└ パス長検査"),
        (sip_builder, "check_unicode_normalization", "└ NFC 検査"),
        (bagit, "make_bag", "BagIt 化"),
        # AIP
        (sip_reader, "read", "SIP 読み取り"),
        (fixity, "verify", "完全性確認"),
        (aip_pipeline, "_normalize_all", "正規化"),
        (mets, "build_mets", "METS 生成"),
        (aip_pipeline, "_build", "AIP 組み立て(合計)"),
    ]

    saved = [(owner, name, getattr(owner, name)) for owner, name, _ in targets]
    try:
        for owner, name, label in targets:
            _wrap(owner, name, label, timings)
        yield
    finally:
        for owner, name, original in saved:
            setattr(owner, name, original)


def _maxrss_bytes(who: int) -> int:
    """ru_maxrss をバイトで返す。

    単位が OS で違う。macOS/BSD はバイト、Linux はキロバイト。
    ここを取り違えると 1024 倍ずれた数字を文書に書くことになる。
    """
    raw = resource.getrusage(who).ru_maxrss
    return raw if sys.platform == "darwin" else raw * 1024


def _first_size(root: Path, name: str) -> int:
    """root 配下で最初に見つかった name のバイト数。無ければ 0。"""
    for found in root.rglob(name):
        return found.stat().st_size
    return 0


def _tree_size(root: Path) -> tuple[int, int]:
    """(ファイル数, 合計バイト)。生成物の大きさを測る。"""
    count = 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
            count += 1
    return count, total


# --------------------------------------------------------------------------
# 1 ケースの実行（子プロセス側）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """測る対象 1 件。"""

    shape: str  # files / bigfile / deep
    scale: int  # files: 件数, bigfile: バイト, deep: 件数
    depth: int = 1
    virus: bool = False
    pii: bool = False
    bag: bool = True
    aip: bool = True

    @property
    def name(self) -> str:
        core = {
            "files": f"files:{self.scale}",
            "bigfile": f"bigfile:{self.scale // (1 << 20)}MiB",
            "deep": f"deep:{self.scale}@{self.depth}",
        }[self.shape]
        flags = "".join(
            [
                "+virus" if self.virus else "",
                "+pii" if self.pii else "",
                "" if self.bag else "+nobag",
                "" if self.aip else "+noaip",
            ]
        )
        return core + flags


def _generate(case: Case, root: Path) -> None:
    if case.shape == "files":
        make_files_tree(root, case.scale)
    elif case.shape == "bigfile":
        make_bigfile_tree(root, case.scale)
    elif case.shape == "deep":
        make_deep_tree(root, case.scale, case.depth)
    else:  # pragma: no cover - CLI で弾いている
        raise ValueError(f"未知の形状: {case.shape}")


def run_case(case: Case, workdir: Path, *, trace: bool) -> dict:
    """1 ケースを最後まで走らせて測定値を返す。子プロセスで呼ばれる。"""
    from archival_packager.core import aip_pipeline, sip_pipeline
    from archival_packager.core.aip_models import AIPOptions
    from archival_packager.core.models import SIPMetadata, SIPOptions

    source = workdir / "source"
    out = workdir / "out"
    source.mkdir(parents=True)
    out.mkdir(parents=True)

    gen_started = time.perf_counter()
    _generate(case, source)
    gen_seconds = time.perf_counter() - gen_started

    timings = Timings()
    metadata = SIPMetadata(identifier=f"BENCH-{case.name}", title="性能計測", date_note="2026")
    options = SIPOptions(
        make_bag=case.bag,
        scan_pii=case.pii,
        scan_virus=case.virus,
        sanitize_filenames=False,
    )

    if trace:
        tracemalloc.start()

    with _instrument(timings):
        sip_started = time.perf_counter()
        sip_result = sip_pipeline.run(
            input_path=source, output_parent=out, metadata=metadata, options=options
        )
        sip_seconds = time.perf_counter() - sip_started

        aip_seconds = 0.0
        aip_files = aip_bytes = mets_bytes = 0
        if case.aip:
            aip_out = workdir / "aip-out"
            aip_out.mkdir()
            aip_started = time.perf_counter()
            aip_result = aip_pipeline.run(
                sip_root=sip_result.sip_path,
                output_parent=aip_out,
                options=AIPOptions(normalize=False, serialize_zip=False),
            )
            aip_seconds = time.perf_counter() - aip_started
            aip_files, aip_bytes = _tree_size(aip_result.aip_path)
            # METS は規模に比例して膨らむ唯一の生成物なので、単体の大きさを残す。
            # 「AIP が何 GB か」では、payload とメタデータのどちらが効いたか分からない。
            mets_bytes = aip_result.mets_path.stat().st_size

    traced_peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()

    sip_files, sip_bytes = _tree_size(sip_result.sip_path)
    _, source_bytes = _tree_size(source)

    return {
        "case": case.name,
        "spec": asdict(case),
        "generate_seconds": gen_seconds,
        "input_files": sip_result.file_count,
        "input_bytes": source_bytes,
        "sip_seconds": sip_seconds,
        "aip_seconds": aip_seconds,
        "sip_output_files": sip_files,
        "sip_output_bytes": sip_bytes,
        "aip_output_files": aip_files,
        "aip_output_bytes": aip_bytes,
        "mets_bytes": mets_bytes,
        "dfxml_bytes": _first_size(sip_result.sip_path, "dfxml.xml"),
        "warnings": len(sip_result.warnings),
        "maxrss_self_bytes": _maxrss_bytes(resource.RUSAGE_SELF),
        "maxrss_children_bytes": _maxrss_bytes(resource.RUSAGE_CHILDREN),
        "tracemalloc_peak_bytes": traced_peak,
        "stages": timings.stages,
        "peak_after": timings.peak_after,
    }


# --------------------------------------------------------------------------
# 親プロセス側
# --------------------------------------------------------------------------


def _spawn(case: Case, *, trace: bool, keep: bool) -> dict:
    """子プロセスで 1 ケースを走らせ、JSON を受け取る。

    ピークメモリを規模ごとに切り離すために分ける（冒頭の説明を参照）。
    """
    args = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--child",
        json.dumps(asdict(case)),
    ]
    if trace:
        args.append("--tracemalloc")
    if keep:
        args.append("--keep")

    proc = subprocess.run(args, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return {
            "case": case.name,
            "spec": asdict(case),
            "error": (proc.stderr or "")[-2000:],
        }

    # 子は最終行に JSON を出す。パイプラインの progress は標準エラーへ流している。
    last = [line for line in proc.stdout.splitlines() if line.strip()][-1]
    return json.loads(last)


def _fmt_bytes(n: float | None) -> str:
    if n is None:
        return "-"
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _print_table(results: list[dict]) -> None:
    print()
    print(f"{'ケース':<28}{'件数':>8}{'SIP 秒':>10}{'AIP 秒':>10}{'最大RSS':>12}{'子RSS':>12}")
    print("-" * 80)
    for r in results:
        if "error" in r:
            print(f"{r['case']:<28}{'失敗':>8}")
            continue
        print(
            f"{r['case']:<28}{r['input_files']:>8}"
            f"{r['sip_seconds']:>10.2f}{r['aip_seconds']:>10.2f}"
            f"{_fmt_bytes(r['maxrss_self_bytes']):>12}"
            f"{_fmt_bytes(r['maxrss_children_bytes']):>12}"
        )

    for r in results:
        if "error" in r:
            print(f"\n### {r['case']} — 失敗\n{r['error']}")
            continue
        print(f"\n### {r['case']} の工程内訳（秒 / その段までのピークメモリ）")
        peaks = r.get("peak_after", {})
        for label, seconds in sorted(r["stages"].items(), key=lambda kv: -kv[1]):
            if seconds < 0.0005:
                continue
            print(f"  {label:<26}{seconds:>9.3f}  {_fmt_bytes(peaks.get(label)):>10}")
        print(
            f"  生成物: SIP {_fmt_bytes(r['sip_output_bytes'])} / "
            f"AIP {_fmt_bytes(r['aip_output_bytes'])} / "
            f"METS {_fmt_bytes(r.get('mets_bytes'))} / "
            f"dfxml {_fmt_bytes(r.get('dfxml_bytes'))} / 警告 {r['warnings']} 件"
        )


# --------------------------------------------------------------------------
# スイート定義
# --------------------------------------------------------------------------

SUITES: dict[str, Callable[[], list[Case]]] = {
    # 仕組みの確認用。数十秒で終わる。
    "smoke": lambda: [Case("files", 100)],
    # 規模の効き方を見る最小限。ウイルス検査の有無も比べる。
    "quick": lambda: [
        Case("files", 100),
        Case("files", 1_000),
        Case("files", 10_000),
        Case("files", 1_000, virus=True),
        Case("files", 1_000, pii=True),
        Case("files", 2_000, depth=1),
        Case("deep", 2_000, depth=12),
        Case("bigfile", 100 << 20),
    ],
    # 実運用の上限を探る。時間がかかる。
    "full": lambda: [
        Case("files", 100),
        Case("files", 1_000),
        Case("files", 10_000),
        Case("files", 50_000),
        Case("files", 10_000, virus=True),
        Case("bigfile", 1 << 30),
    ],
}


def _parse_case(text: str) -> Case:
    """"files:1000" / "deep:2000@12" / "bigfile:100MiB" を Case にする。"""
    shape, _, rest = text.partition(":")
    if shape == "bigfile":
        mib = int(rest.removesuffix("MiB").removesuffix("MB"))
        return Case("bigfile", mib << 20)
    scale, _, depth = rest.partition("@")
    return Case(shape, int(scale), depth=int(depth) if depth else 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--suite", choices=sorted(SUITES), help="あらかじめ決めた組み合わせ")
    parser.add_argument(
        "--case", action="append", default=[], help="files:1000 / deep:2000@12 / bigfile:100MiB"
    )
    parser.add_argument("--virus", action="store_true", help="--case にウイルス検査を付ける")
    parser.add_argument("--pii", action="store_true", help="--case に PII 走査を付ける")
    parser.add_argument("--no-aip", action="store_true", help="AIP 生成を測らない")
    parser.add_argument("--tracemalloc", action="store_true", help="Python 側のピークも測る（時間は伸びる）")
    parser.add_argument("--keep", action="store_true", help="作業ディレクトリを消さない")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child is not None:
        return _run_as_child(args)

    cases = list(SUITES[args.suite]()) if args.suite else []
    for text in args.case:
        base = _parse_case(text)
        cases.append(
            Case(
                base.shape, base.scale, depth=base.depth,
                virus=args.virus, pii=args.pii, aip=not args.no_aip,
            )
        )
    if not cases:
        parser.error("--suite か --case のどちらかが要る")

    results = []
    for case in cases:
        print(f"[{case.name}] 実行中…", file=sys.stderr, flush=True)
        results.append(_spawn(case, trace=args.tracemalloc, keep=args.keep))

    if args.format == "json":
        payload = {"environment": describe_environment(), "results": results}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_table(results)
    return 0


def _run_as_child(args: argparse.Namespace) -> int:
    """子プロセスとして 1 ケースだけ走らせ、結果 JSON を標準出力へ出す。"""
    case = Case(**json.loads(args.child))
    workdir = Path(tempfile.mkdtemp(prefix="archival-packager-bench-"))
    try:
        result = run_case(case, workdir, trace=args.tracemalloc)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        if args.keep:
            print(f"作業ディレクトリを残しました: {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def describe_environment() -> dict:
    """測定環境。数字だけ残しても、どの機械で測ったか分からないと使えない。"""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": _sysctl("machdep.cpu.brand_string"),
        "memory_bytes": _sysctl("hw.memsize"),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
    }


def _sysctl(key: str) -> str | None:
    """macOS の sysctl を 1 項目だけ引く。他 OS では None。"""
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(["sysctl", "-n", key], capture_output=True, encoding="utf-8", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() or None


if __name__ == "__main__":
    raise SystemExit(main())
