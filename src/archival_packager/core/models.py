"""SIP 生成パイプラインの共有データモデル。

現行 Swift 実装の `Sources/SIP/Pipeline.swift` に対応する。各処理段
（フォーマット識別 / チェックサム / PII 走査 / ウイルス検査）が
`ScannedFile` を順に埋めていき、最後に `SIPBuilder` がパッケージを組む。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


@dataclass(slots=True)
class SIPMetadata:
    """記述メタデータ。記述スプレッドシートに事前入力される。"""

    identifier: str  # 資料群/移管の識別子（例: 2026-移管-総務課）
    title: str  # タイトル（必須）
    scope_note: str = ""  # 内容・範囲（scope and content）
    date_note: str = ""  # 年代（例: 2024–2025）


@dataclass(slots=True)
class SIPOptions:
    """パッケージ化オプション。"""

    make_bag: bool = False  # BagIt bag として梱包するか
    prior_accession_path: Path | None = None  # 前回の accession.csv（指定時は arrangement-map.csv を出力）
    scan_pii: bool = False  # 個人情報(PII)候補を走査するか
    scan_virus: bool = False  # ウイルス検査（同梱 clamscan＋取得済み DB があれば実行）
    sanitize_filenames: bool = False  # 受入時にファイル名を安全化するか
    serialize_zip: bool = False  # 生成した SIP を無圧縮 zip に固めて単一ファイル化するか

    # 現行 Swift 版には無い項目。
    #
    # macOS(APFS) と Windows(NTFS) では、同じ見た目のファイル名でも
    # Unicode の正規化形が異なることがある（濁点などが分解されるか合成されるか）。
    # マニフェストや METS に記録する文字列がプラットフォームで揺れると、
    # 「同じ資料から作った SIP なのに突合できない」という事故になる。
    #
    # ただし既定は False にしてある。現行実装との出力一致（差分検証）を
    # 先に成立させるため。両OS での一貫性を優先する運用に切り替える判断は、
    # 差分検証が通ってから明示的に行う。
    normalize_recorded_paths_nfc: bool = False


@dataclass(slots=True)
class PIIFinding:
    """PII 走査の検出結果。値はマスク済みで保持する（原文は残さない）。"""

    kind: str  # 種別（email / phone / mynumber など）
    masked: str  # マスク済みの検出値
    line: int | None = None  # 行番号（分かる場合）


@dataclass(slots=True)
class ScannedFile:
    """入力ツリー内の 1 ファイル。各処理段が情報を埋めていく。"""

    # 入力ルートからの相対パス（POSIX, 区切りは "/"）。
    # sanitize 有効時は安全化後の名前が入る。
    relative_path: str
    # 入力ルートからの絶対パス（コピー・ハッシュ計算用）。
    # sanitize しても元ファイルを指したままにする。
    absolute_path: Path
    size_bytes: int
    modified: datetime

    # sanitize で名前を変更した場合の元の相対パス（未変更なら None）。
    # accession.csv の「原パス（受入時）」に残し、SHA-256 で SIP 内ファイルと突合できる。
    original_relative_path: str | None = None

    # Checksums が埋める
    sha256: str | None = None

    # Siegfried が埋める（フォーマット識別）
    puid: str | None = None  # PRONOM ID（例: fmt/19）
    format_name: str | None = None  # 例: "Acrobat PDF 1.4 - Portable Document Format"
    mime_type: str | None = None  # 例: application/pdf
    format_basis: str | None = None  # 識別根拠（siegfried の basis）
    format_warning: str | None = None  # 例: "extension mismatch" / 未識別

    # PII 走査が埋める（検出があった場合のみ。マスク済み）
    pii: list[PIIFinding] = field(default_factory=list)

    # ウイルス検査が埋める（感染時のみ。ClamAV シグネチャ名）
    virus: str | None = None


@dataclass(slots=True)
class SIPResult:
    """SIP 生成の最終結果。UI とレポート表示に使う。"""

    sip_path: Path  # 生成された SIP（または bag）ディレクトリ
    file_count: int
    total_bytes: int
    bagged: bool
    spreadsheet_path: Path | None = None  # 記述スプレッドシート
    report_path: Path | None = None  # 人間可読サマリ
    arrangement_map_path: Path | None = None  # 配列前後の対応表（prior 指定時のみ）
    pii_report_path: Path | None = None  # PII レポート（検出があった時のみ）
    zip_path: Path | None = None  # 無圧縮 zip（serialize_zip 指定時のみ）
    warnings: list[str] = field(default_factory=list)  # 未識別・拡張子不一致など目視確認したい点


class PipelineErrorKind(Enum):
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_FAILED = "tool_failed"
    NO_INPUT_FILES = "no_input_files"
    IO = "io"


class SIPPipelineError(Exception):
    """パイプラインの失敗。UI にそのまま出せる日本語メッセージを持つ。"""

    def __init__(self, kind: PipelineErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    @classmethod
    def tool_not_found(cls, tool: str) -> SIPPipelineError:
        return cls(PipelineErrorKind.TOOL_NOT_FOUND, f"必要なツールが見つかりません: {tool}")

    @classmethod
    def tool_failed(cls, tool: str, code: int, stderr: str) -> SIPPipelineError:
        return cls(PipelineErrorKind.TOOL_FAILED, f"{tool} が失敗しました (code {code}): {stderr}")

    @classmethod
    def no_input_files(cls) -> SIPPipelineError:
        return cls(PipelineErrorKind.NO_INPUT_FILES, "入力フォルダに対象ファイルがありません。")

    @classmethod
    def io(cls, message: str) -> SIPPipelineError:
        return cls(PipelineErrorKind.IO, f"ファイル操作に失敗しました: {message}")
