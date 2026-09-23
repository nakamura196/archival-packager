"""SIP 生成パイプライン全体のオーケストレーション。

退役した Swift 実装の `Sources/SIP/Orchestrator.swift` に由来する。

入力フォルダ走査 → フォーマット識別 → SHA-256 → 走査（ウイルス/PII）
→ スプレッドシート → SIP/BagIt 組み立て → レポート、の順で駆動する。

`run` はブロッキング処理（FS 走査・ハッシュ計算）を含むため、UI からは
バックグラウンドで呼び、`progress` で進捗文字列を受け取って表示する。
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from . import (
    accession as accession_mod,
)
from . import (
    checksums,
    clamav,
    conversion_registry,
    dfxml,
    document_text,
    filenames,
    image_normalize,
    pii,
    report,
    siegfried,
    sip_builder,
    spreadsheets,
    zip_io,
)
from . import (
    scan as scan_mod,
)
from .aip_models import DerivativePurpose, Executor
from .models import (
    ScannedFile,
    SIPMetadata,
    SIPOptions,
    SIPPipelineError,
    SIPResult,
    is_same_or_inside,
)
from .sip_builder import SIPBuildRequest, SubmissionDocs

Progress = Callable[[str], None]

# PII 走査で 1 ファイルあたり読む上限（8 MiB）。これを超える分は見ない。
PII_SCAN_MAX_BYTES = 8 << 20

#: SIPResult.warnings に載せる、**種類 1 つあたりの**上限。
#:
#: 値は report.py の一覧上限（_LIST_LIMIT = 100）に揃えてある。同じ移管について
#: 画面と report.txt で「何件まで並ぶか」が違うと、読んだ人はどちらが正しいのか
#: 判断できない。100 件あれば、どういう問題がどのあたりで起きているかは十分に伝わる。
#:
#: 上限を設ける理由は Issue #10。未識別のファイルが数万件ある資料群では警告も
#: 数万行になり、画面はその全行を組み立てて描画し、人は誰も読まない。
#:
#: **全体で 1 本の上限にはしない。** 未識別が数万件ある移管では、先頭から詰めると
#: ウイルス検出の 1 行が枠から押し出されて消える。いちばん見落としてはいけない
#: ものから消えることになるので、種類ごとに枠を分ける。
WARNING_LIMIT_PER_KIND = 100


def run(
    *,
    input_path: Path,
    output_parent: Path,
    metadata: SIPMetadata,
    options: SIPOptions,
    progress: Progress = lambda _msg: None,
) -> SIPResult:
    # 何かを書く前に止める（原本のフォルダに SIP を作らせない）。
    if is_same_or_inside(output_parent, input_path):
        raise SIPPipelineError.output_inside_input()

    temp_extract: Path | None = None
    try:
        root, source_note = _resolve_input(input_path, progress)
        if root != input_path:
            temp_extract = root

        # 構造判定。入力直下に objects/ があればその構造を尊重する。
        structured = scan_mod.has_objects_dir(root)
        scan_root = root / "objects" if structured else root
        progress(
            "Archivematica transfer 構造を検出しました（objects/ をそのまま尊重）"
            if structured
            else "入力フォルダを走査しています…"
        )

        files = scan_mod.scan(scan_root)
        if not files:
            raise SIPPipelineError.no_input_files()
        progress(f"対象ファイル: {len(files)} 件")

        files = _maybe_sanitize(files, options, progress)
        files, format_status = _identify_formats(files, root, progress)
        _characterise_images(files, progress)
        _compute_checksums(files, progress)
        virus_status = _scan_virus(files, root, options, progress)
        _scan_pii(files, options, progress)

        docs = _build_documents(
            files, root, scan_root, metadata, options, structured, virus_status,
            format_status, source_note, progress,
        )

        progress("BagIt bag を組み立てています…" if options.make_bag else "SIP を組み立てています…")
        result = sip_builder.build(
            SIPBuildRequest(
                input_root=root,
                output_parent=output_parent,
                files=files,
                metadata=metadata,
                options=options,
            ),
            docs,
        )

        # Windows で開けない長さのパスを警告に足す。macOS で作った SIP を
        # Windows へ渡したときに初めて露見する種類の問題なので、作った側で検出する。
        result.warnings.extend(sip_builder.check_path_lengths(result.sip_path, files))
        result.warnings.extend(
            sip_builder.check_unicode_normalization(
                files, sanitized=options.sanitize_filenames
            )
        )
        result.warnings.extend(_unreadable_image_warnings(files))

        # **最後にまとめて打ち切る。** 途中で切ると、あとから足した警告
        # （パス長・NFC・画像）だけが上限を免れる。
        result.warnings = _bounded_warnings(result.warnings)

        if options.serialize_zip:
            progress("ZIP（無圧縮）に固めています…")
            result.zip_path = zip_io.create_stored(result.sip_path)
            progress(f"ZIP を作成しました: {result.zip_path.name}")

        progress("完了しました。")
        return result

    finally:
        if temp_extract is not None:
            shutil.rmtree(temp_extract.parent, ignore_errors=True)


# --------------------------------------------------------------------------
# 各段
# --------------------------------------------------------------------------


def _resolve_input(input_path: Path, progress: Progress) -> tuple[Path, str]:
    """入力が ZIP なら展開し、実効ルートと受入元の記録を返す。

    受入元 zip のフィキシティ（SHA-256）を report に残す。展開は「変換」なので、
    元の姿を記録しておかないと後から検証できない。
    """
    if not zip_io.is_zip(input_path):
        return input_path, ""

    progress("入力 ZIP を展開しています…")
    digest = checksums.sha256_of(input_path)
    size = input_path.stat().st_size

    temp_parent = Path(tempfile.mkdtemp(prefix="archival-packager-unzip-"))
    dest = temp_parent / "extracted"
    zip_io.extract(input_path, dest)

    note = (
        f"受入元 ZIP: {input_path.name}\n"
        f"  SHA-256: {digest}\n"
        f"  サイズ: {size} バイト"
    )
    return zip_io.effective_root(dest), note


def _maybe_sanitize(
    files: list[ScannedFile], options: SIPOptions, progress: Progress
) -> list[ScannedFile]:
    if not options.sanitize_filenames:
        return files

    sanitized, renamed = filenames.apply(files, normalize_nfc=True)
    progress(
        f"ファイル名サニタイズ: {renamed} 件を安全な名前に変更（元名は accession.csv に保持）"
        if renamed
        else "ファイル名サニタイズ: 変更対象なし"
    )
    return sanitized


def _identify_formats(
    files: list[ScannedFile], root: Path, progress: Progress
) -> tuple[list[ScannedFile], str]:
    """フォーマットを識別し、(ファイル, 何をしたか) を返す。

    **「未識別 54 件」とだけ書かれても、担当者は何をすればよいか分からない。**
    ツールが無くて識別しなかったのか、識別した結果どれにも当てはまらなかったのかで、
    次にやることがまったく違う（前者は入れ直す、後者は目視で確かめる）。
    """
    from . import bundled

    if bundled.find("sf") is None:
        progress("siegfried が同梱されていないため、フォーマット識別をスキップします。")
        return files, "スキップ（siegfried 未同梱。すべて未識別になります）"

    progress("フォーマットを識別しています（siegfried）…")
    try:
        records = siegfried.identify(root)
    except (SIPPipelineError, OSError) as exc:
        # 同梱されていても起動できないことがある。siegfried が配布する mac ビルドは
        # 1 つだけで中身は arm64 だが、こちらのアプリ本体は universal なので
        # Intel Mac でも起動してしまい、そこで "Bad CPU type in executable" になる。
        #
        # フォーマット識別は SIP 作成の必須要素ではない（PUID 欄が空になるだけ）。
        # ここで移管作業全体を落とすのは割に合わないので、警告にして続行する。
        detail = exc.message if isinstance(exc, SIPPipelineError) else str(exc)
        progress(f"フォーマット識別に失敗したためスキップします: {detail}")
        return files, f"スキップ（siegfried を実行できず: {detail[:80]}）"

    # siegfried が返すパス文字列と、こちらが持つ絶対パスを突き合わせる。
    # 表記の揺れ（シンボリックリンク・相対表記）に備えて解決したパスで引く。
    by_path = {str(Path(k).resolve()): v for k, v in records.items()}

    for f in files:
        record = by_path.get(str(f.absolute_path.resolve())) or records.get(str(f.absolute_path))
        if record is None:
            continue
        f.puid = record.puid
        f.format_name = record.format_name
        f.mime_type = record.mime_type
        f.format_basis = record.basis
        f.format_warning = record.warning

    return files, "実施（siegfried / PRONOM）"


def _characterise_images(files: list[ScannedFile], progress: Progress) -> None:
    """画像の技術的特性（画素数・色空間・ビット深度・解像度）を読み、DFXML に残す。

    DFXML が持っているのはファイルとしての事実だけで、**中身が画像であることに
    由来する性質が 1 つも残っていなかった**（Issue #8）。画素数の分からない画像は、
    後から見た人に「これで原本の代わりになるか」を判断させられない。

    対象は**いま TIFF 化の対象にしている画像フォーマットと TIFF 自身**に限る。
    その一覧は変換規則の表（conversion_registry）が持っているので、そちらに聞く。
    ここに PUID の一覧を書き写すと、表に足された PUID が黙って対象から漏れる。

    音声・動画は対象外。Pillow では読めず、新しい依存（MediaInfo / ExifTool 相当）が
    要るため。Issue #8 に残してある。
    """
    targets = [f for f in files if _is_pillow_image(f.puid)]
    if not targets:
        return

    progress(f"画像の技術的特性を読み取っています…（{len(targets)} 件）")
    for f in targets:
        f.image = scan_mod.image_characteristics(f.absolute_path)

    unreadable = sum(1 for f in targets if f.image is not None and not f.image.readable)
    if unreadable:
        # 黙って飛ばさない。壊れた画像は「特性が空の画像」と見分けが付かない。
        progress(
            f"画像 {unreadable} 件は開けず、特性を読み取れませんでした。"
            "理由は dfxml.xml に残し、目視確認の警告にも出します。"
        )


def _is_pillow_image(puid: str | None) -> bool:
    """その PUID は、アプリ内の Pillow で開く画像か。

    TIFF 自身は変換の対象ではない（既に保存に適した形式なので規則が無い）が、
    特性を記録したいのは同じなので明示的に足す。

    **tool の名前だけで判定しない。** 利用者が rules.toml に tool = "pillow" と
    書いた外部コマンドの規則を、アプリ内蔵の画像変換と取り違えないため
    （aip_models.Executor の注を参照）。
    """
    if puid is None:
        return False
    if puid == conversion_registry.TIFF_PUID:
        return True
    rule = conversion_registry.rule_for(puid, DerivativePurpose.PRESERVATION)
    return (
        rule is not None
        and rule.executor is Executor.BUILTIN
        and rule.tool == image_normalize.TOOL
    )


def _unreadable_image_warnings(files: list[ScannedFile]) -> list[str]:
    """開けなかった画像を、目視確認の対象として警告に出す。

    DFXML に書くだけでは、XML を開いた人しか気づけない。壊れた原本が混ざって
    いること自体が担当者の判断材料なので、画面と CLI にも出す。
    """
    return [
        f"画像を読めません: {f.relative_path}（{f.image.error}）"
        for f in files
        if f.image is not None and not f.image.readable
    ]


def _compute_checksums(files: list[ScannedFile], progress: Progress) -> None:
    progress("チェックサム(SHA-256)を計算しています…")
    total = len(files)
    for i, f in enumerate(files, start=1):
        f.sha256 = checksums.sha256_of(f.absolute_path)
        if i == total or i % 25 == 0:
            progress(f"SHA-256: {i}/{total}")


def _scan_virus(
    files: list[ScannedFile], root: Path, options: SIPOptions, progress: Progress
) -> str:
    if not options.scan_virus:
        return "未実施（オプション OFF）"

    if clamav.find_tool() is None:
        progress("ClamAV が同梱されていないため、ウイルスチェックをスキップします。")
        return "スキップ（ClamAV 未同梱）"

    database = clamav.database_directory()
    if not clamav.has_database(database):
        progress("ウイルス定義 DB が未取得のため、ウイルスチェックをスキップします。")
        return "スキップ（定義 DB 未取得）"

    progress("ウイルスチェック中（ClamAV）…")
    try:
        findings = clamav.scan(root)
    except SIPPipelineError as exc:
        # スキャンの失敗で SIP 作成全体は止めない（警告にして続行）。
        progress(f"ウイルスチェックに失敗しましたが、SIP 作成は続行します: {exc.message}")
        return f"失敗（続行）: {exc.message}"

    # ここまで来たら検査は実行できた。検出の有無にかかわらず印を付ける。
    # 「検査していない」と「検査して検出なし」を区別するため。
    for f in files:
        f.scanned_for_virus = True

    if not findings:
        progress("ウイルスは検出されませんでした。")
        return "実施（検出なし）"

    by_path = {str(Path(p).resolve()): sig for p, sig in findings.items()}
    for f in files:
        f.virus = by_path.get(str(f.absolute_path.resolve()))

    progress(f"ウイルス検出: {len(findings)} 件。report.txt を確認してください。")
    return f"実施（検出 {len(findings)} 件）"


def _scan_pii(files: list[ScannedFile], options: SIPOptions, progress: Progress) -> None:
    if not options.scan_pii:
        return

    progress("個人情報(PII)をスキャンしています…")
    hit_files = 0
    for f in files:
        text = document_text.scannable(f, max_bytes=PII_SCAN_MAX_BYTES)
        if text is None:
            # 文書のはずなのに中身を取り出せなかったものは、黙って飛ばさない。
            # 壊れた PDF や暗号化された PDF を「候補なし」に混ぜると、
            # 個人情報が入っていても「見つかりませんでした」と表示される。
            f.pii_unreadable = document_text.is_pdf(f)
            continue
        f.pii_scanned = True
        findings = pii.scan(text)
        if findings:
            f.pii = findings
            hit_files += 1

    total = sum(len(f.pii) for f in files)
    unreadable = sum(1 for f in files if f.pii_unreadable)
    head = (
        "PII 候補は見つかりませんでした。"
        if hit_files == 0
        else f"PII 候補: {total} 件（{hit_files} ファイル）。pii-report.csv を確認してください。"
    )
    if unreadable:
        head += f" ただし {unreadable} ファイルは中身を読めず、走査できていません。"
    progress(head)


def _bounded_warnings(warnings: list[str]) -> list[str]:
    """種類ごとに先頭 N 件だけ残し、あふれた分は「（他 N 件）」の 1 行にまとめる。

    **件数は落とさない。** 「多いので省略しました」では、担当者は残りが 3 件なのか
    3 万件なのか分からず、次に何をすべきかも決められない。何件あふれたかを必ず書く。
    考え方も文言も report.py の `_truncated` に揃えてある（同じ移管について、
    画面と report.txt で見え方が変わらないようにするため）。

    並びは元のまま（ファイル順）にし、あふれた分の行だけを末尾に足す。
    種類ごとに寄せ直すと、これまで出ていた順序が理由もなく変わる。
    """
    kept: list[str] = []
    counts: dict[str, int] = {}
    for warning in warnings:
        kind = _warning_kind(warning)
        counts[kind] = counts.get(kind, 0) + 1
        if counts[kind] <= WARNING_LIMIT_PER_KIND:
            kept.append(warning)

    for kind, count in counts.items():
        rest = count - WARNING_LIMIT_PER_KIND
        if rest <= 0:
            continue
        # 前置きを残す。CLI は前置きで検出の種類を分けており（cli._FINDING_PREFIXES）、
        # 前置きの無い行はどの種類の話なのか分からなくなる。
        kept.append(f"{kind}: （他 {rest} 件）" if kind else f"（他 {rest} 件）")
    return kept


def _warning_kind(warning: str) -> str:
    """警告の種類。「種類: 対象」という前置きで見る（無ければ空文字）。

    前置きは sip_builder.collect_warnings などが付けている。ここで前置きを
    読めなくなっても打ち切り自体は働く（すべて 1 つの種類として数えられる）ので、
    表示が崩れるだけで、警告が消えることはない。
    """
    head, separator, _rest = warning.partition(": ")
    return head if separator else ""


def _build_documents(
    files: list[ScannedFile],
    root: Path,
    scan_root: Path,
    metadata: SIPMetadata,
    options: SIPOptions,
    structured: bool,
    virus_status: str,
    format_status: str,
    source_note: str,
    progress: Progress,
) -> SubmissionDocs:
    progress("スプレッドシートを生成しています…")

    # 構造化入力で metadata/metadata.csv があればそれを継承する。
    # 担当者が既に記入したものを、こちらの空テンプレートで上書きしてはならない。
    provided = None
    if structured:
        candidate = root / "metadata" / "metadata.csv"
        if candidate.is_file():
            try:
                provided = candidate.read_text(encoding="utf-8")
                progress("入力の metadata/metadata.csv を継承します")
            except OSError:
                provided = None

    if provided is not None:
        # 中身はそのまま、filename 列の基点だけ bag / 非 bag に合わせ直す。
        # bag のとき "objects/…" のままだと、Archivematica から見て転送内に
        # 実体の無いパスになり、記入済みの記述がどのファイルにも紐づかない。
        provided = spreadsheets.rebase_metadata_csv(provided, bagged=options.make_bag)

    arrangement_map = _arrangement_map(files, options, progress)

    return SubmissionDocs(
        description_csv=spreadsheets.description(files, metadata),
        atom_import_csv=spreadsheets.atom_import(files, metadata),
        formats_csv=spreadsheets.formats(files),
        accession_csv=spreadsheets.accession(files),
        metadata_csv=provided or spreadsheets.metadata_template(files, bagged=options.make_bag),
        dfxml_xml=dfxml.build(files, scan_root).decode("utf-8"),
        report_text=report.text_report(
            files, metadata, options, virus_status=virus_status,
            format_status=format_status, source_archive_note=source_note
        ),
        report_html=report.html_report(
            files, metadata, options, virus_status=virus_status,
            format_status=format_status,
        ),
        arrangement_map_csv=arrangement_map,
        pii_csv=spreadsheets.pii_report(files) if options.scan_pii else None,
    )


def _arrangement_map(
    files: list[ScannedFile], options: SIPOptions, progress: Progress
) -> str | None:
    """前回の accession.csv が指定されていれば突合して対応表を作る。"""
    prior_path = options.prior_accession_path
    if prior_path is None:
        return None

    try:
        prior_text = prior_path.read_text(encoding="utf-8")
    except OSError:
        progress(f"前回の accession.csv を読めませんでした: {prior_path.name}。対応表をスキップします。")
        return None

    prior = accession_mod.parse(prior_text)
    rows = accession_mod.join(files, prior)
    matched = sum(1 for r in rows if r.matched)
    progress(f"配列前後を突合: {matched}/{len(rows)} 行が一致（arrangement-map.csv）")
    return accession_mod.to_csv(rows)
