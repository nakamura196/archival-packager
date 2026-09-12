"""SIP（または BagIt bag）の組み立て。

退役した Swift 実装の `Sources/SIP/SIPBuilder.swift` に由来する。
本家 sipcreator のレイアウトに倣う（折衷）:

    非bag: <pkg>/objects/...
           <pkg>/metadata/metadata.csv          ← AM 規約: file 単位 DC は metadata/ 直下
           <pkg>/metadata/checksum.sha256       ← AM 規約: 外部チェックサムは metadata/ 直下
           <pkg>/metadata/submissionDocumentation/{description,atom-import,formats,accession}.csv
                                                 {arrangement-map,pii-report}.csv（該当時のみ）
                                                 dfxml.xml, report.txt, report.html, checksum.sha256
    bag:   <pkg>/data/objects/...
           <pkg>/data/metadata/...
           <pkg>/{bagit.txt, bag-info.txt, manifest-sha256.txt, tagmanifest-sha256.txt}

チェックサムは本家の MD5 ではなく SHA-256 を用いる（Swift 版から引き継いだ改善）。

## checksum.sha256 が 2 箇所にあるのはなぜか（2026-09-12）

読み手が 2 人いて、求める形が違うため。**同じ内容をパス基準だけ変えて 2 本置く。**

    metadata/checksum.sha256                        Archivematica が読む。
                                                    行は "<hash>  <objects/ からの相対パス>"。
                                                    仕様の例（beihai.tif）に objects/ は付かない。
    metadata/submissionDocumentation/checksum.sha256 このアプリの core/fixity.py が読む。
                                                    行は "<hash>  objects/<相対パス>"（SIP ルート基準）。

前者だけにすると、このアプリ自身の完全性確認がマニフェストを見つけられず
「検査していない」状態になる。後者だけにすると、Archivematica の
*Verify transfer checksums* が走らず、検証されずに素通りする。
どちらも同じ 1 つのリストから作るので、内容がずれることはない。

## BagIt は自前実装しない

Swift 版は bagit.txt / manifest / tagmanifest を手書きしていた。ここでは
米国議会図書館の `bagit` パッケージ（BagIt 仕様のリファレンス実装）に委ねる。
Python で書き直した最大の理由がこれで、次の 2 点が効く。

1. **仕様の細部を自前で持たなくてよい。** 例えば BagIt 仕様は、ファイル名に
   LF / CR / % を含む場合マニフェスト中でパーセントエンコードすることを要求する。
   Swift 版はこれを行っておらず、該当する資料名では仕様非適合の bag ができていた。
2. **検証ができる。** `bagit.Bag(path).validate()` で、生成した bag が
   仕様に適合し、かつペイロードがマニフェストと一致することを確認できる。

副作用として bag-info.txt の `Bag-Software-Agent` は bagit.py のものになる。
Swift 版との差分検証でも、Bagging-Date と併せて除外して比較していた
（どちらも生成時刻・生成主体を示す情報で、パッケージの中身とは独立）。
"""

from __future__ import annotations

import os
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .models import ScannedFile, SIPMetadata, SIPOptions, SIPPipelineError, SIPResult

BOM = "﻿"

# submissionDocumentation の位置（metadata/ の下）。
SUBDOC_PARTS = ("metadata", "submissionDocumentation")

# パッケージ名に使えない文字。パス区切りとドライブ指定を潰す。
_UNSAFE_IN_NAME = set("/\\:")


@dataclass(slots=True)
class SubmissionDocs:
    """submissionDocumentation に書き出す中身。

    生成は各モジュール（DescriptiveSpreadsheet / Accession / DFXML / HTMLReport）が担い、
    ビルダーは配置とマニフェストだけを担当する。
    """

    description_csv: str
    formats_csv: str
    accession_csv: str
    metadata_csv: str
    dfxml_xml: str
    report_text: str
    report_html: str
    arrangement_map_csv: str | None = None
    pii_csv: str | None = None
    #: AtoM の csv:import に渡す CSV。既定を空にしてあるのは、この引数を
    #: 知らない既存の呼び出し（テストを含む）をそのまま通すため。
    #: 空なら atom-import.csv 自体を作らない。
    atom_import_csv: str = ""


@dataclass(slots=True)
class SIPBuildRequest:
    input_root: Path
    output_parent: Path
    files: list[ScannedFile]
    metadata: SIPMetadata
    options: SIPOptions


def build(request: SIPBuildRequest, docs: SubmissionDocs) -> SIPResult:
    """SIP ディレクトリを組み立て、結果を返す。"""
    if not request.files:
        raise SIPPipelineError.no_input_files()

    pkg_dir = _make_package_dir(request)
    total_bytes = sum(f.size_bytes for f in request.files)
    warnings = collect_warnings(request.files)

    if request.options.make_bag:
        paths = _build_bag(request, pkg_dir, docs, total_bytes)
    else:
        paths = _build_plain(request, pkg_dir, docs)

    _normalize_permissions(pkg_dir)

    return SIPResult(
        sip_path=pkg_dir,
        file_count=len(request.files),
        total_bytes=total_bytes,
        bagged=request.options.make_bag,
        spreadsheet_path=paths["description"],
        report_path=paths["report"],
        arrangement_map_path=paths.get("arrangement_map"),
        pii_report_path=paths.get("pii_report"),
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# 非 Bag レイアウト
# --------------------------------------------------------------------------


def _build_plain(request: SIPBuildRequest, pkg_dir: Path, docs: SubmissionDocs) -> dict[str, Path]:
    objects = pkg_dir / "objects"
    subdoc = pkg_dir.joinpath(*SUBDOC_PARTS)
    objects.mkdir(parents=True, exist_ok=True)
    subdoc.mkdir(parents=True, exist_ok=True)

    _copy_payload(request.files, objects)
    paths = _write_submission_docs(subdoc, docs)
    _write_checksum_files(request.files, subdoc)

    return paths


# --------------------------------------------------------------------------
# Bag レイアウト
# --------------------------------------------------------------------------


def _build_bag(
    request: SIPBuildRequest, pkg_dir: Path, docs: SubmissionDocs, total_bytes: int
) -> dict[str, Path]:
    """ペイロードを組んでから bagit で bag 化する。

    bagit.make_bag は「対象ディレクトリの中身を data/ へ移し、タグファイルを書く」
    という破壊的操作を行う。したがって先に pkg_dir 直下へ objects/ と metadata/ を
    普通に作り、最後に一度だけ bag 化する。
    """
    import bagit

    objects = pkg_dir / "objects"
    subdoc = pkg_dir.joinpath(*SUBDOC_PARTS)
    objects.mkdir(parents=True, exist_ok=True)
    subdoc.mkdir(parents=True, exist_ok=True)

    _copy_payload(request.files, objects)
    paths = _write_submission_docs(subdoc, docs)

    # bag-info.txt に載せる項目。Swift 版と同じ意味の項目を渡す。
    # Bagging-Date と Payload-Oxum は bagit 側が算出するので渡さない
    # （渡すと二重に書かれる）。
    bag_info: dict[str, str] = {}
    if request.metadata.identifier:
        bag_info["External-Identifier"] = request.metadata.identifier
    if request.metadata.title:
        bag_info["External-Description"] = request.metadata.title

    try:
        bagit.make_bag(str(pkg_dir), bag_info=bag_info, checksums=["sha256"])
    except Exception as exc:  # bagit は独自例外を投げる
        raise SIPPipelineError.io(f"bag 化に失敗: {exc}") from exc

    # bag 化で中身が data/ 配下へ移動したので、返すパスを追従させる。
    return {k: _moved_into_data(v, pkg_dir) for k, v in paths.items() if v is not None}


def _moved_into_data(path: Path, pkg_dir: Path) -> Path:
    """bag 化前のパスを data/ 配下のパスへ読み替える。"""
    return pkg_dir / "data" / path.relative_to(pkg_dir)


def validate_bag(pkg_dir: Path) -> None:
    """生成した bag が BagIt 仕様に適合しているか検証する。

    自前実装ではこれができなかった。マニフェストとペイロードの不一致、
    タグファイルの欠落、チェックサムの誤りをここで捕まえられる。
    """
    import bagit

    try:
        bag = bagit.Bag(str(pkg_dir))
        bag.validate()
    except Exception as exc:
        raise SIPPipelineError.io(f"bag の検証に失敗: {exc}") from exc


# --------------------------------------------------------------------------
# submissionDocumentation
# --------------------------------------------------------------------------


def _write_submission_docs(subdoc: Path, docs: SubmissionDocs) -> dict[str, Path]:
    """提出書類群（BOM 付き CSV と report）を書き、主要なパスを返す。"""
    description = subdoc / "description.csv"
    _write_csv(docs.description_csv, description)
    if docs.atom_import_csv:
        _write_atom_csv(docs.atom_import_csv, subdoc / "atom-import.csv")
    _write_csv(docs.formats_csv, subdoc / "formats.csv")
    _write_csv(docs.accession_csv, subdoc / "accession.csv")

    # metadata.csv は Archivematica 規約に合わせ metadata/ 直下
    # （submissionDocumentation の親）に置く。担当者がここに DC を書き込むと、
    # AIP 作成時に file 単位 dmdSec として反映される。
    _write_csv(docs.metadata_csv, subdoc.parent / "metadata.csv")

    _write_text(docs.dfxml_xml, subdoc / "dfxml.xml")
    report = subdoc / "report.txt"
    _write_text(docs.report_text, report)
    _write_text(docs.report_html, subdoc / "report.html")

    out: dict[str, Path] = {"description": description, "report": report}
    if arrangement := _write_optional_csv(docs.arrangement_map_csv, "arrangement-map.csv", subdoc):
        out["arrangement_map"] = arrangement
    if pii := _write_optional_csv(docs.pii_csv, "pii-report.csv", subdoc):
        out["pii_report"] = pii
    return out


def _write_checksum_files(files: list[ScannedFile], subdoc: Path) -> None:
    """外部チェックサムを 2 箇所に書く（内容は同じ・パス基準だけ違う）。

    分けている理由はモジュール冒頭の注に書いた。bag のときは呼ばない。
    bag は manifest-sha256.txt が同じ役目を果たすので、二重に持つと
    食い違ったときにどちらが正なのか分からなくなる。
    """
    digests = [(f.sha256, f.relative_path) for f in files if f.sha256]

    # Archivematica 用。transfer.rst の例は "<hash>␣␣beihai.tif" で、
    # objects/ の接頭辞は付かない（パスは objects/ からの相対）。
    _write_text(
        _manifest_lines([f"{sha}  {rel}" for sha, rel in digests]),
        subdoc.parent / "checksum.sha256",
    )
    # このアプリの fixity.py 用。SIP ルート基準なので objects/ が要る。
    # 公開済みの版が作った SIP もこの形で、読み手（fixity.py）は変えられない。
    _write_text(
        _manifest_lines([f"{sha}  objects/{rel}" for sha, rel in digests]),
        subdoc / "checksum.sha256",
    )


def _write_optional_csv(csv: str | None, name: str, subdoc: Path) -> Path | None:
    """中身があれば書いてパスを返す。無ければ None（ファイル自体を作らない）。"""
    if not csv:
        return None
    path = subdoc / name
    _write_csv(csv, path)
    return path


# --------------------------------------------------------------------------
# 補助
# --------------------------------------------------------------------------


def collect_warnings(files: list[ScannedFile]) -> list[str]:
    """目視確認したい点を集める。UI とレポートに出す。"""
    warnings: list[str] = []
    for f in files:
        if f.virus:
            warnings.append(f"ウイルス検出: {f.relative_path} ({f.virus})")
        if f.pii:
            warnings.append(f"PII候補: {f.relative_path} ({len(f.pii)})")
        if f.puid is None:
            warnings.append(f"未識別: {f.relative_path}")
        elif f.format_warning and "mismatch" in f.format_warning.lower():
            warnings.append(f"拡張子不一致: {f.relative_path}")
    return warnings


def _make_package_dir(request: SIPBuildRequest) -> Path:
    """パッケージ名をサニタイズし、衝突しないディレクトリを作る。"""
    raw = _first_non_empty(request.metadata.identifier, request.metadata.title)
    base = _sanitize_package_name(raw)

    candidate = request.output_parent / base
    counter = 2
    while candidate.exists():
        candidate = request.output_parent / f"{base} {counter}"
        counter += 1

    try:
        candidate.mkdir(parents=True)
    except OSError as exc:
        raise SIPPipelineError.io(f"ディレクトリ作成に失敗: {candidate} ({exc})") from exc
    return candidate


def _sanitize_package_name(s: str) -> str:
    import unicodedata

    mapped = "".join(
        "_" if (ch in _UNSAFE_IN_NAME or unicodedata.category(ch) == "Cc") else ch for ch in s
    )
    return mapped.strip() or "SIP"


def _first_non_empty(*values: str) -> str:
    for v in values:
        if v.strip():
            return v
    return "SIP"


def _copy_payload(files: list[ScannedFile], base: Path) -> None:
    """入力ファイルを相対パスを保って base 配下へコピーする。"""
    for f in files:
        dest = base / f.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            # copy2 でタイムスタンプを保つ。受入資料の更新日時は記録対象の情報。
            shutil.copy2(f.absolute_path, dest)
        except OSError as exc:
            raise SIPPipelineError.io(f"コピーに失敗: {f.relative_path} ({exc})") from exc


def _normalize_permissions(root: Path) -> None:
    """権限正規化（本家準拠: dir 755 / file 644）。

    Windows には POSIX パーミッションが無いので何もしない。
    """
    if os.name == "nt":
        return

    root.chmod(0o755)
    for path in root.rglob("*"):
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            # 1 ファイルの権限設定に失敗してもパッケージ自体は成立する。
            pass


def _manifest_lines(lines: list[str]) -> str:
    """マニフェストの本文を作る。空なら空文字（末尾改行も付けない）。"""
    return "\n".join(lines) + "\n" if lines else ""


def _write_csv(csv: str, path: Path) -> None:
    """UTF-8 BOM 付きで書く。Excel が UTF-8 と判定するために BOM が要る。"""
    _write_bytes((BOM + csv).encode("utf-8"), path)


def _write_atom_csv(csv: str, path: Path) -> None:
    """AtoM に渡す CSV は **BOM を付けない**。

    AtoM の CSV validation は UTF-8 の BOM を ERROR にはしないが、剥がすとは
    どこにも書いていない。剥がされなければ先頭の列名が "﻿ legacyId" になり、
    **未知の列として黙って捨てられる**（未知列は警告だけで無視される）。
    legacyId を出す目的そのものが消えるので、ここでは BOM を付けない。

    この CSV は人が Excel で開くためのものではない。人が開くのは
    同じフォルダの description.csv の方で、そちらは BOM 付きのまま。
    改行も CRLF ではなく LF（spreadsheets.LF の注を参照）。
    """
    _write_bytes(csv.encode("utf-8"), path)


def _write_text(text: str, path: Path) -> None:
    _write_bytes(text.encode("utf-8"), path)


def _write_bytes(data: bytes, path: Path) -> None:
    try:
        # newline 変換を避けるためバイナリで書く。Windows でテキストモードだと
        # LF が CRLF に化け、マニフェストのハッシュが環境依存になる。
        path.write_bytes(data)
    except OSError as exc:
        raise SIPPipelineError.io(f"書き込みに失敗: {path} ({exc})") from exc


# 参考: Windows の MAX_PATH (260) は、入力の相対パスだけでなく
# ユーザが選ぶ出力先の深さにも依存する。ここで全体長を検査して警告する。
_MAX_PATH_WINDOWS = 260


def check_path_lengths(pkg_dir: Path, files: list[ScannedFile]) -> list[str]:
    """Windows で開けなくなる長さのパスを警告として返す。

    macOS で作った SIP を Windows へ渡したときに初めて露見する種類の問題なので、
    生成した側で検出しておく。
    """
    warnings: list[str] = []
    for f in files:
        full = pkg_dir / "objects" / f.relative_path
        if len(str(full)) >= _MAX_PATH_WINDOWS:
            warnings.append(f"パスが長すぎます（Windows で開けない可能性）: {f.relative_path}")
    return warnings


def check_unicode_normalization(files: list[ScannedFile], *, sanitized: bool) -> list[str]:
    """記録名の Unicode 正規化形が NFC でないファイルを警告として返す。

    check_path_lengths と同じ「作った側では露見しない移送先の問題」を扱う。

    macOS は濁点付きの仮名などを分解形(NFD)で返すことがある。この名前のまま
    マニフェストに書くと、APFS は正規化に鈍感なので macOS 上では一致するが、
    NTFS / ext4 では別名として扱われ、照合が失敗する。作った環境でだけ通る
    パッケージになるので、作った側で気づけるようにする。

    記録名だけを黙って NFC に直すことはしない。実ファイル名が NFD のままだと
    今度は「マニフェストに書いてあるファイルが無い」bag になるため。名前を
    揃えたい場合はファイル名の安全化（sanitize）を使う。実ファイルごと
    NFC の名前で書き出されるので、記録と実体が食い違わない。
    """
    offenders = [
        f.relative_path
        for f in files
        if unicodedata.normalize("NFC", f.relative_path) != f.relative_path
    ]
    if not offenders:
        return []

    warnings = [
        f"ファイル名が NFC 正規化されていません（Windows/Linux で別名と判定される可能性）: {rel}"
        for rel in offenders
    ]
    if not sanitized:
        warnings.insert(
            0,
            f"Unicode 正規化形が NFC でないファイル名が {len(offenders)} 件あります。"
            "「ファイル名を安全化する」を有効にすると、実ファイルごと NFC に統一されます。",
        )
    return warnings
