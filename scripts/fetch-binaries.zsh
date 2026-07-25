#!/usr/bin/env zsh
# 同梱する外部バイナリを取得して binaries/<os>/ に置く。
#
# 使い方: ./scripts/fetch-binaries.zsh
#
# リポジトリにバイナリを含めない代わりに、取得を再現可能にするためのスクリプト。
# バージョンは固定する。フォーマット識別の結果が環境で変わると、
# 同じ資料から作った保存パッケージの再現性が崩れるため。

set -euo pipefail

# mac / Windows で同一版に揃えること。
SIEGFRIED_VERSION="1-11-6"

case "$(uname -s)" in
  Darwin) OSDIR="macos"; SF_ASSET="mac64" ;;
  *)      print -u2 "このスクリプトは macOS 用です。Windows は CI で取得します（.github/workflows）。"; exit 1 ;;
esac

DEST="binaries/${OSDIR}"
mkdir -p "${DEST}"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

ver="${SIEGFRIED_VERSION//-/.}"

print "[1/2] siegfried ${ver} (${SF_ASSET})"
curl -fsSL -o "${WORK}/sf.zip" \
  "https://github.com/richardlehane/siegfried/releases/download/v${ver}/siegfried_${SIEGFRIED_VERSION}_${SF_ASSET}.zip"
unzip -o -j "${WORK}/sf.zip" -d "${DEST}" >/dev/null
chmod +x "${DEST}"/sf*

print "[2/2] 署名 DB (default.sig)"
# default.sig を同梱しないと、配布先で siegfried が署名 DB を見つけられない。
# 開発機では Homebrew 版のものを拾って動いてしまうため、この欠落は気づきにくい。
# data zip の中では siegfried/ 配下に入っているのでパターンにディレクトリを含める。
curl -fsSL -o "${WORK}/data.zip" \
  "https://github.com/richardlehane/siegfried/releases/download/v${ver}/data_${SIEGFRIED_VERSION}.zip"
unzip -o -j "${WORK}/data.zip" '*/default.sig' -d "${DEST}" >/dev/null
[[ -f "${DEST}/default.sig" ]] || { print -u2 "default.sig を取得できませんでした"; exit 1 }

print
print "取得しました:"
ls -la "${DEST}"
print
print "注: ClamAV / Ghostscript / ImageMagick は未対応。"
print "    必要になった時点で同じ方針（バージョン固定・取得の再現性）で追加する。"
