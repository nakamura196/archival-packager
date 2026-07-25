"""ZIP の読み書き。

現行 Swift 実装の `Sources/SIP/ZipImport.swift` と `ZipArchive.swift` に対応する。

Swift 版は `/usr/bin/unzip` と `/usr/bin/zip` を起動していた。どちらも Windows には
存在しないため、標準ライブラリ `zipfile` に置き換える。外部プロセスが要らなくなり、
同梱すべきバイナリも 1 つ減る。

## 展開時のパス検証

外部から受け取った zip には、`../` を含むエントリや絶対パスのエントリが入りうる
（zip-slip）。受入対象の資料は「素性が分からないもの」であることが前提なので、
展開先の外に書き出さないことを明示的に検証する。
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from .models import SIPPipelineError


def is_zip(path: Path) -> bool:
    """zip として開けるか（拡張子ではなく中身で判定する）。"""
    if not path.is_file():
        return False
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def extract(archive: Path, dest: Path) -> None:
    """zip を dest へ展開する。dest の外に出るエントリは拒否する。"""
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()

    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = (resolved_dest / info.filename).resolve()
                if not target.is_relative_to(resolved_dest):
                    # 展開先の外を指すエントリ。黙って飛ばすと「展開できたのに
                    # ファイルが足りない」状態になるので、明示的に失敗させる。
                    raise SIPPipelineError.io(
                        f"ZIP に展開先の外を指すエントリが含まれています: {info.filename}"
                    )
            zf.extractall(resolved_dest)
    except SIPPipelineError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise SIPPipelineError.io(f"ZIP を展開できません: {archive} ({exc})") from exc


def effective_root(extracted: Path) -> Path:
    """展開結果の実効ルートを返す。

    zip の中身が単一ディレクトリで包まれている場合（よくある）、そのディレクトリを
    ルートとして扱う。そうしないと SIP の objects/ が余計に 1 段深くなる。
    """
    entries = [p for p in extracted.iterdir() if p.name not in {"__MACOSX", ".DS_Store"}]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extracted


def create_stored(directory: Path, dest: Path | None = None) -> Path:
    """ディレクトリを無圧縮（store）の zip に固める。

    無圧縮なのは、中身が既に圧縮済みの資料（画像・動画）で時間だけ掛かるのを避け、
    かつ将来この zip 自体からファイル単位で取り出しやすくするため。
    """
    target = dest or directory.with_suffix(directory.suffix + ".zip")

    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as zf:
            # 並び順を固定する。走査順に任せると同じ内容から違うバイト列の zip が出る。
            for path in sorted(directory.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                # directory 自身の名前を含める（展開すると 1 段の包みになる）。
                arcname = Path(directory.name) / path.relative_to(directory)
                zf.write(path, arcname.as_posix())
    except OSError as exc:
        raise SIPPipelineError.io(f"ZIP を作成できません: {target} ({exc})") from exc

    return target
