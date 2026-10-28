"""Kokoro で原稿を読み、行ごとの wav を書く。record_guide.py から呼ばれる。

Kokoro は torch などの重い依存を持つので、アプリの環境には入れない。
動画の道具（~/git/blog）が使っている Kokoro の環境の python で、これを走らせる。
読みの置き換え（共通の表・算用数字をかなに）も、その道具の部品をそのまま使う。

    <Kokoro の python> kokoro_say.py <ja|en> <入出力の JSON>

入出力の JSON は [{"text": 読む文, "out": 書き出す wav}, ...]。
各行の Kokoro 自身の読み（音素）を "phonemes" に足して書き戻す。読みの点検に使う。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

COMMON = Path(os.environ.get("KOKORO_COMMON", Path.home() / "git/blog/common/kokoro"))
sys.path.insert(0, str(COMMON))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from kokoro_core import GAP, SR, VOICE, fix, get_pipe, load_readings, split_sentences  # noqa: E402


def main() -> int:
    lang, jobs_path = sys.argv[1], Path(sys.argv[2])
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    # 案件ごとの読みの表は持たない（narration.json の say に仮名で書いてある）。共通の表だけ使う。
    rd = load_readings(Path("/nonexistent"), lang)
    pipe = get_pipe(lang)
    for job in jobs:
        spoken = fix(job["text"], rd, lang)
        chunks, phon = [], []
        for sen in split_sentences(spoken, lang):
            for _, ps, au in pipe(sen, voice=VOICE[lang]):
                chunks.append(au.numpy())
                phon.append(ps)
            chunks.append(np.zeros(int(SR * GAP), dtype=np.float32))
        sf.write(job["out"], np.concatenate(chunks[:-1]), SR)
        job["spoken"], job["phonemes"] = spoken, " ".join(phon)
    jobs_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
