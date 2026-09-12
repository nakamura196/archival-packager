"""AIP 生成パイプラインの共有モデル。

退役した Swift 実装の `Sources/AIP/AIPPipeline.swift` に由来する。

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


class CheckOutcome(Enum):
    """派生物を開き直せたかどうか。"""

    PASSED = "passed"
    FAILED = "failed"
    #: 読み戻す道具を持っていない形式だった。**「確認して通った」と混同させない。**
    SKIPPED = "skipped"


@dataclass(slots=True)
class DerivativeCheck:
    """生成した派生物を開き直した結果。PREMIS の validation event として書き出す。

    **これは形式の適合性検査ではない。** Archivematica は veraPDF で PDF/A の
    適合を、JHOVE で形式の well-formed / valid を見ている。どちらも Java 製で
    同梱できないため（NOTICE と README の「外部ツールの同梱方針」）、ここで
    行っているのは同梱済みの Pillow / pypdf で**開き直せるか**だけである。

    それでも記録する価値があるのは、「変換したつもりで、実は読めないものが
    できていた」が最も気づきにくい壊れ方だからである。読めないものを保存用と
    して記録するくらいなら、変換できなかったと記録するほうがまだ良い。
    """

    outcome: CheckOutcome
    #: 何を見て、何が分かったか（PREMIS の eventOutcomeDetailNote に入る）。
    note: str
    #: 開き直しに使った道具と版（例 "Pillow 12.3.0 (libtiff 4.7.0)"）。
    #: **版まで書く。** 読めた／読めなかったは、その版の挙動でしかない。
    #: 道具を持たずに飛ばしたときは空。
    agent: str = ""


@dataclass(slots=True)
class Derivative:
    """正規化で生成された派生物（保存用 or 利用用）。"""

    purpose: DerivativePurpose
    path: Path  # 生成先の絶対パス（objects/ 配下）
    relative_path: str  # 入力 objects/ ルートからの相対パス
    size_bytes: int
    uuid: str
    tool_name: str  # 生成に使ったツールの呼び名（"pillow" / "gs"）
    command_line: str
    sha256: str | None = None
    puid_out: str | None = None
    format_name_out: str | None = None
    #: どの正規化ルールが動いたか（NormalizationRule.rule_id）。
    #: PREMIS の eventDetail に出す。Archivematica の FPRCommandID に相当する。
    rule_id: str = ""
    #: ツールの版（例 "Ghostscript 10.07.1" / "Pillow 12.3.0 (libtiff 4.7.0)"）。
    #: **同じ規則でも、版が違えば出てくるバイト列は違う。** 版が無いと、
    #: 後からこの派生物を再現できるかどうかを判断できない。
    #: 聞けなかった場合は空（「聞いていない」ではなく「答えなかった」を意味する）。
    tool_version: str = ""
    #: その規則が組み込みか、利用者が足したものか（RuleSource の値）。
    #: **rule_id に "(user)" のように混ぜ込まない。** rule_id は過去の AIP に
    #: そのまま書き込まれている文字列で、後年それと突き合わせるためにある。
    #: 装飾を足すと、同じ規則で作った古い AIP と新しい AIP の値が食い違う。
    #: 別の欄に持ち、PREMIS では別の属性として書く。
    rule_source: str = ""
    #: 派生物を開き直した結果。**None は「まだ確かめていない」を意味する**
    #: （normalizer が必ず付ける。テストが手で組んだ Derivative では付かない）。
    #: 「確かめて通った」は CheckOutcome.PASSED で表す。両者を混ぜないこと。
    check: DerivativeCheck | None = None


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


class Executor(Enum):
    """規則を誰が実行するか。

    **この 2 つしかない。** コードを差し込む道（プラグイン）は用意しない。
    公証・ストア審査を通した配布物が、実行時に外部のコードを読むことになるため。
    """

    #: アプリ内蔵の処理（現状は Pillow の画像変換）。利用者の表からは指定できない。
    BUILTIN = "builtin"
    #: 利用者の環境にある外部コマンドを呼ぶ。利用者が足せるのはこちらだけ。
    COMMAND = "command"


class RuleSource(Enum):
    """その規則がどこから来たか。

    保存の観点でこれは記録に値する。**同じ資料から別の組織が別の AIP を作った
    とき、違いの原因が「表を足したから」なのかどうかを、後から見た人が
    判断できる必要がある。**
    """

    BUILTIN = "builtin"
    USER = "user"


@dataclass(slots=True)
class NormalizationRule:
    """1 つの正規化ルール。PUID をキーに「どのツールでどう変換するか」を定める。"""

    puid_in: str
    purpose: DerivativePurpose
    tool: str  # 変換の担い手。外部バイナリ名（gs）か、アプリ内変換の識別子（pillow）
    args: list[str]  # 引数テンプレート（{in} {out} を実パスに置換）
    out_extension: str  # 出力ファイルの拡張子（例: "pdf"）
    #: 規則の安定した識別子（例 "image-to-tiff"）。Archivematica が PREMIS の
    #: eventDetail に書く FPRCommandID に相当する。**版を上げても変えないこと。**
    #: 変えると、過去に作った AIP に書かれた値と突き合わせられなくなり、
    #: 「どの規則で作られたか」を追えなくなる。
    #: 既定を空にしてあるのは、その場限りの規則（テストや実験）まで識別子を
    #: 強要しないため。表に載る規則が空のまま出ていかないことは
    #: conversion_registry のテストで固定する。
    rule_id: str = ""
    puid_out: str | None = None  # 出力フォーマットの PUID（分かれば）
    #: 出力フォーマットの名前。**PUID だけだと画面に「unknown」と出る。**
    #: 変換したものが未識別に見えるのは、実態と違ううえ紛らわしい。
    format_name_out: str | None = None
    #: 誰が実行するか。**tool の名前で分岐しない。** 利用者が tool = "pillow" と
    #: 書いた外部コマンドの規則を、アプリ内蔵の画像変換と取り違えないため。
    #: 既定を COMMAND にしてあるのは、内蔵処理が既定で呼ばれる状態を作らないため。
    executor: Executor = Executor.COMMAND
    #: 組み込みか、利用者が足したものか。PREMIS に書き出す。
    #: 既定が BUILTIN なのは、コードの中で組み立てた規則は定義上 BUILTIN だから
    #: （利用者の表から来た規則は rule_table が必ず USER を明示して作る）。
    source: RuleSource = RuleSource.BUILTIN


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
