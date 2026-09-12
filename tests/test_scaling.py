"""規模に対する回帰テスト（既定では走らない）。

普段のテストは「正しい成果物が出るか」を見ている。ここが見るのは
**大きな入力で破綻しないこと**。数千ファイルで重くなったという報告が
過去にあり、そのときは UI 側だけ直してパイプラインは測っていなかった。
同じことを繰り返さないための番人。

## 走らせ方

    ARCHIVAL_PACKAGER_SCALING=1 uv run pytest tests/test_scaling.py -v

既定では skip する。2,000 件の SIP と AIP を実際に作るので数十秒かかり、
毎回の `pytest` に入れると誰も回さなくなる。

## なぜ `@pytest.mark.slow` ではなく `skipif` なのか

pyproject.toml の `markers` に登録していないマーカーを使うと
`PytestUnknownMarkWarning` が出る。このリポジトリは `filterwarnings = error`
なので、警告がそのまま失敗になる。pyproject は触らない約束なので、
環境変数と `skipif` で制御する。

## なぜ子プロセスで測るのか

ピークメモリは `resource.getrusage` の `ru_maxrss` で見るが、これは
プロセスの生涯最高水位なので、pytest 本体や他のテストが使った分まで
混ざる。scripts/benchmark.py は 1 ケース = 1 子プロセスで測るので、
その値をそのまま使う。合成データの作り方も 1 か所に保てる。

## 上限の決め方

実測（Apple M4 Max / macOS 26.6 / Python 3.12.13、2026-09-12）は
2,000 件で SIP 1.98 秒・AIP 1.94 秒・ピーク 113 MiB。上限はここから
時間で約 10 倍、メモリで約 3 倍の余裕を取った。時間の余裕を大きく取るのは、
CI の共有ランナーや HDD の機械では簡単に数倍になるため。
**本命は時間の絶対値ではなく、下の「件数を 4 倍にしたときの伸び」のほう。**
こちらは機械の速さに依らないので、計算量が線形から外れたら必ず捕まる。
上限は環境変数で上書きできる（遅い機械で回すときに使う）。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

RUN = os.environ.get("ARCHIVAL_PACKAGER_SCALING") == "1"

pytestmark = pytest.mark.skipif(
    not RUN,
    reason="規模テストは既定で走らせない（ARCHIVAL_PACKAGER_SCALING=1 で有効）",
)

MIB = 1 << 20

#: 中規模の基準。実運用で「よくある移管」の上のほうを想定した件数。
MEDIUM_FILES = 2_000
#: 伸び方を見るための小さいほう。MEDIUM_FILES の 1/4。
SMALL_FILES = 500

# 上限。遅い機械では環境変数で緩める。
MAX_SIP_SECONDS = float(os.environ.get("ARCHIVAL_PACKAGER_SCALING_MAX_SIP_SECONDS", "25"))
MAX_AIP_SECONDS = float(os.environ.get("ARCHIVAL_PACKAGER_SCALING_MAX_AIP_SECONDS", "25"))
MAX_PEAK_MIB = float(os.environ.get("ARCHIVAL_PACKAGER_SCALING_MAX_PEAK_MIB", "340"))

#: 件数を 4 倍にしたときに許す伸び。線形なら 4 倍、二乗なら 16 倍になる。
#: 6 倍で切ると、線形＋多少のばらつきは通し、二乗は確実に捕まえる。
MAX_GROWTH_FOR_4X = 6.0


def _load_benchmark():
    """scripts/benchmark.py を読み込む。

    scripts/ はパッケージではないので普通の import はできない。
    合成データの作り方と測り方を 2 か所に書かないために、
    ファイルパスから直接読み込む。
    """
    path = Path(__file__).resolve().parent.parent / "scripts" / "benchmark.py"
    spec = importlib.util.spec_from_file_location("_ap_benchmark", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 置き場所を変えたときだけ
        pytest.fail(f"ベンチマークスクリプトを読み込めません: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def benchmark():
    return _load_benchmark()


@pytest.fixture(scope="module")
def medium(benchmark) -> dict:
    """2,000 件を 1 回だけ測り、モジュール内で使い回す。"""
    return _measure(benchmark, MEDIUM_FILES)


@pytest.fixture(scope="module")
def small(benchmark) -> dict:
    return _measure(benchmark, SMALL_FILES)


def _measure(benchmark, count: int) -> dict:
    case = benchmark.Case("files", count)
    result = benchmark._spawn(case, trace=False, keep=False)
    if "error" in result:
        pytest.fail(f"{case.name} の計測に失敗しました:\n{result['error']}")
    return result


class TestMediumScale:
    """2,000 件が一定の時間とメモリに収まること。"""

    def test_sip_finishes_within_budget(self, medium):
        assert medium["input_files"] == MEDIUM_FILES
        assert medium["sip_seconds"] < MAX_SIP_SECONDS, (
            f"SIP 生成が {medium['sip_seconds']:.1f} 秒（上限 {MAX_SIP_SECONDS} 秒）。"
            f"工程内訳: {_slowest(medium)}"
        )

    def test_aip_finishes_within_budget(self, medium):
        assert medium["aip_seconds"] < MAX_AIP_SECONDS, (
            f"AIP 生成が {medium['aip_seconds']:.1f} 秒（上限 {MAX_AIP_SECONDS} 秒）。"
            f"工程内訳: {_slowest(medium)}"
        )

    def test_peak_memory_within_budget(self, medium):
        peak_mib = medium["maxrss_self_bytes"] / MIB
        assert peak_mib < MAX_PEAK_MIB, (
            f"ピークメモリが {peak_mib:.0f} MiB（上限 {MAX_PEAK_MIB:.0f} MiB）。"
            f"メモリを押し上げた段: {_peak_stage(medium)}"
        )


class TestGrowth:
    """件数を 4 倍にしたときの伸び。

    絶対値と違い、ここは機械の速さに依らない。**線形を超える変更が
    入ったら、速い機械でも遅い機械でも同じように失敗する。**
    """

    def test_time_grows_no_worse_than_linear_ish(self, small, medium):
        ratio = medium["sip_seconds"] / max(small["sip_seconds"], 1e-6)
        assert ratio < MAX_GROWTH_FOR_4X, (
            f"件数 4 倍で SIP 生成が {ratio:.1f} 倍になりました"
            f"（{SMALL_FILES} 件 {small['sip_seconds']:.2f} 秒 → "
            f"{MEDIUM_FILES} 件 {medium['sip_seconds']:.2f} 秒）。"
            f"線形なら 4 倍前後です。工程内訳: {_slowest(medium)}"
        )

    def test_memory_grows_no_worse_than_linear_ish(self, small, medium):
        # インタプリタ本体の常駐分は件数に依らないので、差し引いてから比べる。
        # 引かないと、固定費に薄められて二乗の伸びを見落とす。
        baseline = 25 * MIB
        small_marginal = max(small["maxrss_self_bytes"] - baseline, 1)
        medium_marginal = max(medium["maxrss_self_bytes"] - baseline, 1)
        ratio = medium_marginal / small_marginal
        assert ratio < MAX_GROWTH_FOR_4X, (
            f"件数 4 倍でピークメモリが {ratio:.1f} 倍になりました"
            f"（{small['maxrss_self_bytes'] / MIB:.0f} MiB → "
            f"{medium['maxrss_self_bytes'] / MIB:.0f} MiB）。"
            f"メモリを押し上げた段: {_peak_stage(medium)}"
        )


class TestOutputSize:
    """生成物の大きさ。METS だけは件数にそのまま比例して膨らむ。"""

    def test_mets_stays_proportional(self, medium):
        # 実測 6.1 KiB/件（2026-09-12）。1 件あたり 12 KiB を超えたら、
        # METS に何かを足したということ。数万件では GB 級の XML になるので、
        # 足す前に気づけるようにする。
        per_file = medium["mets_bytes"] / MEDIUM_FILES
        assert per_file < 12 * 1024, (
            f"METS が 1 件あたり {per_file / 1024:.1f} KiB になっています"
            f"（合計 {medium['mets_bytes'] / MIB:.1f} MiB）。"
        )


def _slowest(result: dict, n: int = 5) -> str:
    ordered = sorted(result["stages"].items(), key=lambda kv: -kv[1])[:n]
    return ", ".join(f"{label} {seconds:.2f}s" for label, seconds in ordered)


def _peak_stage(result: dict) -> str:
    """ピークメモリを最も押し上げた段を返す。

    `peak_after` はその段を抜けた時点の最高水位。値が最大の段が、
    そこまでで最もメモリを積んだ段である。
    """
    peaks = result.get("peak_after") or {}
    if not peaks:
        return "（不明）"
    label, value = max(peaks.items(), key=lambda kv: kv[1])
    return f"{label} で {value / MIB:.0f} MiB"
