"""PII 走査用のテキスト抽出。

退役した Swift 実装の `Sources/SIP/DocumentText.swift` に由来する。

既定では PII 走査はプレーンテキストだけが対象で、PDF はバイナリとしてスキップされて
いた（PDF 中のメール・電話番号等が検査されなかった）。PDF はテキストを抽出してから
走査する。画像 PDF・暗号化 PDF は抽出不可で None。

macOS 専用の PDFKit の代わりに pypdf を使う（これも Python を選んだ理由の一つ）。
"""

from __future__ import annotations

from pathlib import Path

from .models import ScannedFile

# PRONOM の PDF 系 PUID（PDF 1.x / PDF/A / PDF/X の主要どころ）。
_PDF_PUIDS: frozenset[str] = frozenset(
    {
        "fmt/14", "fmt/15", "fmt/16", "fmt/17", "fmt/18", "fmt/19", "fmt/20",  # PDF 1.0-1.6
        "fmt/95", "fmt/354", "fmt/476", "fmt/477", "fmt/478",
        "fmt/479", "fmt/480", "fmt/481",                                        # PDF/A
        "fmt/144", "fmt/145", "fmt/146", "fmt/147", "fmt/148",
        "fmt/157", "fmt/158",                                                   # PDF/X
        "fmt/276",                                                              # PDF 1.7
    }
)

# 走査に使う文字コードの候補。日本の現場では Shift_JIS の資料が現役なので必ず試す。
_ENCODINGS = ("utf-8", "cp932", "euc_jp")


def is_pdf(f: ScannedFile) -> bool:
    """拡張子・MIME・PUID のいずれかで PDF と判定する（siegfried 未同梱でも拡張子で拾う）。"""
    if f.absolute_path.suffix.lower() == ".pdf":
        return True
    if f.mime_type and "pdf" in f.mime_type.lower():
        return True
    if f.puid and f.puid in _PDF_PUIDS:
        return True
    return False


def scannable(f: ScannedFile, *, max_bytes: int) -> str | None:
    """PII 走査対象のテキストを取り出す。取得できなければ None。"""
    if is_pdf(f):
        return pdf_text(f.absolute_path, max_bytes=max_bytes)
    return plain_text(f.absolute_path, max_bytes=max_bytes)


def plain_text(path: Path, *, max_bytes: int) -> str | None:
    """テキストとして復号できれば返す。バイナリなら None。"""
    try:
        raw = path.read_bytes()[:max_bytes]
    except OSError:
        return None

    # NUL を含むものはバイナリと見なす（テキスト判定の実用的な近似）。
    if b"\x00" in raw:
        return None

    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def pdf_text(path: Path, *, max_bytes: int) -> str | None:
    """PDF からテキストを抽出する。暗号化で開けない/本文が無ければ None。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # 空パスワードで開けることがある。試して駄目なら諦める。
            try:
                if reader.decrypt("") == 0:
                    return None
            except Exception:
                return None

        parts: list[str] = []
        size = 0
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                # 1 ページの抽出失敗で全体を諦めない。
                continue
            parts.append(text)
            size += len(text.encode("utf-8"))
            if size >= max_bytes:
                break
    except Exception:
        # 壊れた PDF は珍しくない。走査できないだけで、受入自体は続ける。
        return None

    joined = "\n".join(parts).strip()
    return joined or None
