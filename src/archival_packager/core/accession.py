"""受入記録の解析と、配列前後の突合。

現行 Swift 実装の `Sources/SIP/Accession.swift` に対応する。

受入時点（配列前）に書いた accession.csv を後から読み込み、配列後のファイル群と
突き合わせて「配列前パス → 配列後パス」の対応表（arrangement-map.csv）を作る。
突合キーは内容ハッシュ（SHA-256）。原本は一切変更しない。

内容が完全に同一のファイルは原パスの対応が一意に定まらない
（内容が等価なので保存・完全性の観点では実害は小さい）。

## CSV の解析も標準ライブラリに任せる

Swift 版は RFC 4180 風のパーサを手書きしていた。標準の csv モジュールは
引用符・二重化・引用内の改行をすべて扱うので、自前実装をやめる。
特に「引用フィールド内の改行」は手書きの行分割では原理的に扱えない
（Swift 版は先に行で切っているため、値に改行を含む accession.csv を正しく読めない）。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from .models import ScannedFile

CRLF = "\r\n"

COL_ORIGINAL_PATH = "原パス（受入時）"
COL_SHA256 = "SHA-256"


@dataclass(slots=True, frozen=True)
class AccessionRecord:
    """受入記録 1 行ぶん。"""

    original_path: str
    sha256: str


def parse(text: str) -> list[AccessionRecord]:
    """accession.csv の本文をパースする。

    列見出しで引くため、列順の変更や列追加に強い。BOM は除去する。
    """
    body = text.lstrip("﻿")
    if not body.strip():
        return []

    reader = csv.reader(io.StringIO(body, newline=""))
    rows = list(reader)
    if not rows:
        return []

    header = rows[0]

    def index_of(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    i_path = index_of(COL_ORIGINAL_PATH)
    i_hash = index_of(COL_SHA256)

    out: list[AccessionRecord] = []
    for fields in rows[1:]:
        def at(i: int | None, fields: list[str] = fields) -> str:
            return fields[i] if i is not None and i < len(fields) else ""

        digest = at(i_hash)
        if not digest:
            # ハッシュが無い行は突合に使えないので落とす。
            continue
        out.append(AccessionRecord(original_path=at(i_path), sha256=digest))
    return out


# --------------------------------------------------------------------------
# 配列前後の対応表
# --------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ArrangementRow:
    before: str  # 配列前パス（prior の原パス。未突合なら ""）
    after: str  # 配列後パス（現ファイルの相対パス。配列前のみなら ""）
    key_kind: str  # "SHA-256" / "未突合"
    key: str  # 突合に使ったキー値
    matched: bool


def join(current: list[ScannedFile], prior: list[AccessionRecord]) -> list[ArrangementRow]:
    """現在のファイル群（配列後）を prior 受入記録（配列前）に SHA-256 で突合する。

    内容重複時は未消費の prior を 1 件消費する
    （どの物理ファイルかは一意に定まらないが内容は等価）。
    """
    by_sha: dict[str, list[int]] = {}
    for i, r in enumerate(prior):
        if r.sha256:
            by_sha.setdefault(r.sha256, []).append(i)

    consumed: set[int] = set()
    rows: list[ArrangementRow] = []

    for f in current:
        candidate = None
        if f.sha256:
            for pi in by_sha.get(f.sha256, []):
                if pi not in consumed:
                    candidate = pi
                    break

        if candidate is not None:
            consumed.add(candidate)
            rows.append(
                ArrangementRow(
                    before=prior[candidate].original_path,
                    after=f.relative_path,
                    key_kind="SHA-256",
                    key=f.sha256 or "",
                    matched=True,
                )
            )
        else:
            # 受入記録に無い＝新規追加など
            rows.append(
                ArrangementRow(
                    before="", after=f.relative_path,
                    key_kind="未突合", key=f.sha256 or "", matched=False,
                )
            )

    # 突合されなかった prior（配列前にあったが現ファイルに無い＝欠落?）
    for i, r in enumerate(prior):
        if i not in consumed:
            rows.append(
                ArrangementRow(
                    before=r.original_path, after="",
                    key_kind="未突合", key=r.sha256, matched=False,
                )
            )

    return rows


def to_csv(rows: list[ArrangementRow]) -> str:
    """対応表を CSV に整形する（BOM は書き出し側で付与・CRLF）。"""
    out: list[list[str]] = [["配列前パス", "配列後パス", "突合キー種別", "キー", "判定"]]
    for r in rows:
        if r.matched:
            verdict = "一致"
        elif not r.after:
            verdict = "配列前のみ（欠落?）"
        else:
            verdict = "未突合（新規?）"
        out.append([r.before, r.after, r.key_kind, r.key, verdict])

    buf = io.StringIO()
    csv.writer(buf, lineterminator=CRLF, quoting=csv.QUOTE_MINIMAL).writerows(out)
    return buf.getvalue()
