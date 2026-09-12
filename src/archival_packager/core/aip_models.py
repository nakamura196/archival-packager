"""AIP 生成パイプラインの共有モデル。

現行 Swift 実装の `Sources/AIP/AIPPipeline.swift` に対応する。

入力 = SIP Creator が出した SIP ディレクトリ または BagIt bag。
出力 = Archivematica 風 AIP（BagIt bag、data/METS.xml に PREMIS を埋める）。
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# --------------------------------------------------------------------------
# 入力 / オプション
# --------------------------------------------------------------------------


@dataclass(slots=True)
class AIPInput:
    """AIP 化の入力。SIP Creator の出力ディレクトリを指す。"""

    sip_root: Path  # 非 bag なら objects/ を含む dir、bag なら data/ を含む bag ルート
    is_bag: bool  # BagIt bag か。オーケストレータが判定して埋める。


@dataclass(slots=True)
class AIPOptions:
    normalize: bool = True  # フォーマット変換を行うか。False なら原本をそのまま objects/ へ
    compress: bool = False  # AIP を圧縮するか（当面は非圧縮 bag）
    serialize_zip: bool = False  # 無圧縮 zip に固めて単一ファイル化するか
    archivist_name: str = ""  # 担当者名（PREMIS agent に human として記録。空なら付けない）
    descriptive: DescriptiveMetadata | None = None  # GUI で入力・追記した記述メタデータ


# --------------------------------------------------------------------------
# 記述メタデータ（dmdSec 用）
# --------------------------------------------------------------------------


@dataclass(slots=True)
class DescriptiveMetadata:
    """AIP の dmdSec に Dublin Core として出す記述メタデータ。

    SIP の description.csv（AtoM/ISAD(G) シート）から継承する。
    値が無いフィールドは METS に出さない。
    """

    identifier: str | None = None  # -> dc:identifier
    title: str | None = None  # -> dc:title
    creator: str | None = None  # -> dc:creator（Archive Creator）
    date: str | None = None  # -> dc:date（Date expression / start–end）
    description: str | None = None  # -> dc:description（Scope and content）
    extent: str | None = None  # -> dcterms:extent（Extent and medium）
    language: str | None = None  # -> dc:language
    access_rights: str | None = None  # -> dcterms:accessRights

    @property
    def has_any(self) -> bool:
        """出すべき値が 1 つでもあるか（無ければ dmdSec を作らない）。"""
        return any(
            v
            for v in (
                self.identifier, self.title, self.creator, self.date,
                self.description, self.extent, self.language, self.access_rights,
            )
        )

    @staticmethod
    def merge(
        user: DescriptiveMetadata | None, inherited: DescriptiveMetadata | None
    ) -> DescriptiveMetadata | None:
        """GUI 入力を優先し、空欄は description.csv 継承値で埋める。"""
        if user is None and inherited is None:
            return None

        def pick(u: str | None, i: str | None) -> str | None:
            return u if u else i

        u = user or DescriptiveMetadata()
        i = inherited or DescriptiveMetadata()
        merged = DescriptiveMetadata(
            identifier=pick(u.identifier, i.identifier),
            title=pick(u.title, i.title),
            creator=pick(u.creator, i.creator),
            date=pick(u.date, i.date),
            description=pick(u.description, i.description),
            extent=pick(u.extent, i.extent),
            language=pick(u.language, i.language),
            access_rights=pick(u.access_rights, i.access_rights),
        )
        return merged if merged.has_any else None


# --------------------------------------------------------------------------
# 完全性確認
# --------------------------------------------------------------------------


class FixityOutcome(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class FixityStatus:
    """SIP の BagIt manifest 照合結果。"""

    outcome: FixityOutcome
    checked: int = 0  # passed のとき照合した件数
    mismatches: list[str] = field(default_factory=list)  # failed のとき不一致の相対パス
    reason: str = ""  # skipped のとき理由（manifest 無し等）

    @classmethod
    def passed(cls, checked: int) -> FixityStatus:
        return cls(FixityOutcome.PASSED, checked=checked)

    @classmethod
    def failed(cls, mismatches: list[str]) -> FixityStatus:
        return cls(FixityOutcome.FAILED, mismatches=mismatches)

    @classmethod
    def skipped(cls, reason: str) -> FixityStatus:
        return cls(FixityOutcome.SKIPPED, reason=reason)


# --------------------------------------------------------------------------
# PREMIS
# --------------------------------------------------------------------------


class AgentKind(Enum):
    SOFTWARE = "software"
    ORGANIZATION = "organization"
    HUMAN = "human"


@dataclass(slots=True)
class PremisAgent:
    """PREMIS agent（本アプリ / 各ツール / 担当者）。"""

    id: str
    name: str
    kind: AgentKind


@dataclass(slots=True)
class PremisEvent:
    """PREMIS event（ingestion / fixity check / normalization 等）。"""

    type: str  # 例: "fixity check", "normalization", "format identification"
    date_time: str  # ISO8601
    detail_note: str  # eventOutcomeDetailNote 相当
    outcome: str  # 例: "pass" / "fail" / "success"
    agent_ids: list[str] = field(default_factory=list)
    # event 自体の識別子。Swift 版は XML 生成のたびに UUID を振っていたため、
    # 同じ入力から 2 回生成すると別の値になり差分検証ができなかった。
    # ここではモデル側に持たせ、生成を決定的にできるようにする。
    identifier: str = field(default_factory=lambda: str(_uuid.uuid4()))


# --------------------------------------------------------------------------
# ファイルと派生物
# --------------------------------------------------------------------------


class DerivativePurpose(Enum):
    PRESERVATION = "preservation"
    ACCESS = "access"


@dataclass(slots=True)
class Derivative:
    """正規化で生成された派生物（保存用 or 利用用）。"""

    purpose: DerivativePurpose
    path: Path  # 生成先の絶対パス（objects/ 配下）
    relative_path: str  # 入力 objects/ ルートからの相対パス
    size_bytes: int
    uuid: str
    tool_name: str  # 生成に使ったツール（PREMIS event / agent 用）
    command_line: str
    sha256: str | None = None
    puid_out: str | None = None
    format_name_out: str | None = None


@dataclass(slots=True)
class AIPFile:
    """AIP に入る 1 原本ファイルと、その正規化派生物・由来情報。"""

    relative_path: str  # 入力 objects/ ルートからの相対パス（POSIX）
    absolute_path: Path
    size_bytes: int
    uuid: str  # PREMIS objectIdentifier

    # SIP の manifest / accession.csv から継承する受入時ハッシュ
    sha256: str | None = None
    # siegfried（SIP 段）から継承するフォーマット識別
    puid: str | None = None
    format_name: str | None = None
    mime_type: str | None = None
    #: SIP の formats.csv にあったウイルス検査の状態。PREMIS に記録するために運ぶ。
    virus_state: str | None = None

    # metadata.csv の該当行から継承する file 単位の記述メタデータ
    descriptive: DescriptiveMetadata | None = None

    derivatives: list[Derivative] = field(default_factory=list)
    events: list[PremisEvent] = field(default_factory=list)


# --------------------------------------------------------------------------
# 変換ルール
# --------------------------------------------------------------------------


@dataclass(slots=True)
class NormalizationRule:
    """1 つの正規化ルール。PUID をキーに「どのツールでどう変換するか」を定める。"""

    puid_in: str
    purpose: DerivativePurpose
    tool: str  # 変換の担い手。外部バイナリ名（gs）か、アプリ内変換の識別子（pillow）
    args: list[str]  # 引数テンプレート（{in} {out} を実パスに置換）
    out_extension: str  # 出力ファイルの拡張子（例: "pdf"）
    puid_out: str | None = None  # 出力フォーマットの PUID（分かれば）
    #: 出力フォーマットの名前。**PUID だけだと画面に「unknown」と出る。**
    #: 変換したものが未識別に見えるのは、実態と違ううえ紛らわしい。
    format_name_out: str | None = None


# --------------------------------------------------------------------------
# 結果 / エラー
# --------------------------------------------------------------------------


@dataclass(slots=True)
class AIPResult:
    aip_path: Path  # 生成された AIP（bag）ディレクトリ
    aip_uuid: str
    mets_path: Path  # data/METS.<uuid>.xml
    original_count: int
    derivative_count: int
    fixity: FixityStatus
    warnings: list[str] = field(default_factory=list)
    zip_path: Path | None = None


class AIPErrorKind(Enum):
    NOT_A_SIP = "not_a_sip"
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_FAILED = "tool_failed"
    NO_OBJECTS = "no_objects"
    IO = "io"


class AIPPipelineError(Exception):
    def __init__(self, kind: AIPErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message

    @classmethod
    def not_a_sip(cls, message: str) -> AIPPipelineError:
        return cls(AIPErrorKind.NOT_A_SIP, f"SIP として認識できません: {message}")

    @classmethod
    def tool_not_found(cls, tool: str) -> AIPPipelineError:
        return cls(AIPErrorKind.TOOL_NOT_FOUND, f"変換ツールが見つかりません: {tool}")

    @classmethod
    def tool_failed(cls, tool: str, code: int, stderr: str) -> AIPPipelineError:
        return cls(AIPErrorKind.TOOL_FAILED, f"{tool} が失敗しました (code {code}): {stderr}")

    @classmethod
    def no_objects(cls) -> AIPPipelineError:
        return cls(AIPErrorKind.NO_OBJECTS, "objects/ に対象ファイルがありません。")

    @classmethod
    def io(cls, message: str) -> AIPPipelineError:
        return cls(AIPErrorKind.IO, f"ファイル操作に失敗しました: {message}")
