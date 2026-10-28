#!/usr/bin/env zsh
# 同梱する外部バイナリを取得して binaries/macos/ に置く。
#
# 使い方: ./scripts/fetch-binaries.zsh
#
# リポジトリにバイナリを含めない代わりに、取得を再現可能にするためのスクリプト。
# バージョンは固定する。フォーマット識別やウイルス検査の結果が環境で変わると、
# 同じ資料から作った保存パッケージの再現性が崩れるため。
#
# Windows 側は scripts/fetch-binaries.ps1（CI から呼ぶ）。同じバージョンを使う。
#
# ここで同梱しないもの
# --------------------
#   Ghostscript (PostScript/EPS → PDF)
#     AGPL-3.0。MIT のこのアプリに同梱すると、配布物全体のライセンスをどう扱うか
#     という判断が要る（Artifex は商用ライセンスを別途販売している）。加えて
#     macOS 向けの公式ビルドが配布されておらず、ソースからの構築が必要になる。
#     Swift 版でも同梱しておらず、PATH 上の gs を使う方式だった。同じ扱いにする。
#     gs が無い環境では PostScript/EPS は変換されず、原本がそのまま保存され、
#     report に「変換に失敗（原本のまま保存）」が残る。
#
#   veraPDF (PDF/A の適合性検査)
#     いまの変換規則が作る PDF は PDF/A ではない（gs に -dPDFA を渡していない）。
#     veraPDF を入れても必ず「不適合」と出るだけなので、PDF/A で出すように
#     変えるまで入れない。PDF の形式検査は JHOVE の PDF-hul で行う。
#
#   ImageMagick (画像 → TIFF)
#     不要になった。Pillow でアプリ内変換する（src/.../image_normalize.py 参照）。
#     macOS 向けの公式な再配布可能ビルドが無く、両OSで別ビルドになると
#     同じ資料から出る派生物のバイト列が揃わない、というのが決め手。

set -euo pipefail

SIEGFRIED_VERSION="1-11-6"
CLAMAV_VERSION="1.5.3"

# JHOVE（変換した派生物の形式検査）と、それを動かす Java。
# Java は Temurin の JDK を取り、jlink で要るモジュールだけの実行環境を作る。
# JRE をそのまま入れると約 150MB、絞ると約 50MB（2026-10-27 実測）。
# 要るモジュールは jdeps で調べた（jhove-apps / jhove-core / tiff-hul / pdf-hul）。
#
# **ハッシュを固定する。** 配布物に入って利用者の機械で動くので、取得元が
# 差し替えられたら気づける形にしておく。版を上げるときはハッシュも書き換える。
# Windows 側（fetch-binaries.ps1）と版を揃えること。
TEMURIN_VERSION="21.0.12+8"
TEMURIN_SHA256="021d629349ebc12a409faa517b837ec80ceee8f58a5ac85c788ecad07ca6881c"  # aarch64 mac jdk
JRE_MODULES="java.base,java.desktop,java.logging"
JHOVE_JARS=(
  # Maven Central 上のパス                                        SHA-256
  "jhove-apps/1.34.0/jhove-apps-1.34.0.jar 35dd738db51befe430ce48e3567f7f70a99ed1f14834fa23962b8e790cc9881b"
  "jhove-core/1.34.0/jhove-core-1.34.0.jar 337a0a14c335272335f41f375118a51f7e025d5b016c5ee59e7869043d361dd0"
  "modules/tiff-hul/1.9.5/tiff-hul-1.9.5.jar c0ce300c28db5553bbc706a5190e433adf5b7c12a478795b01b3f38747ea0170"
  "modules/pdf-hul/1.12.8/pdf-hul-1.12.8.jar c5acf4e2c84aec6575390bcc43d9e015c6237b75eec5f1f354de7102e6e023bb"
)

case "$(uname -s)" in
  Darwin) ;;
  *) print -u2 "このスクリプトは macOS 用です。Windows は scripts/fetch-binaries.ps1。"; exit 1 ;;
esac

DEST="binaries/macos"
mkdir -p "${DEST}"
WORK=$(mktemp -d)
trap 'rm -rf "${WORK}"' EXIT

sf_ver="${SIEGFRIED_VERSION//-/.}"

# ---------------------------------------------------------------- siegfried

print "[1/6] siegfried ${sf_ver}"
curl -fsSL -o "${WORK}/sf.zip" \
  "https://github.com/richardlehane/siegfried/releases/download/v${sf_ver}/siegfried_${SIEGFRIED_VERSION}_mac64.zip"
# sf だけ。同梱の roy（署名 DB の作成ツール）は実行時に使わない。
# 入れると 11MB 増える上に、署名・公証の対象が 1 つ増える。
unzip -o -j "${WORK}/sf.zip" 'sf' -d "${DEST}" >/dev/null
chmod +x "${DEST}/sf"
rm -f "${DEST}/roy"

# 配布されている mac ビルドは 1 つだけで、中身は arm64。Intel Mac では
# 起動しない（アプリ本体は universal なので Intel でも動いてしまう）。
# アプリ側は識別に失敗しても止まらず「識別をスキップ」に落ちる。
if ! file -b "${DEST}/sf" | grep -q arm64; then
  print "  注意: sf のアーキテクチャが想定と違います: $(file -b "${DEST}/sf")"
fi

print "[2/6] siegfried 署名 DB (default.sig)"
# default.sig を同梱しないと、配布先で siegfried が署名 DB を見つけられない。
# 開発機では Homebrew 版のものを拾って動いてしまうため、この欠落は気づきにくい。
# data zip の中では siegfried/ 配下に入っているのでパターンにディレクトリを含める。
curl -fsSL -o "${WORK}/data.zip" \
  "https://github.com/richardlehane/siegfried/releases/download/v${sf_ver}/data_${SIEGFRIED_VERSION}.zip"
unzip -o -j "${WORK}/data.zip" '*/default.sig' -d "${DEST}" >/dev/null
[[ -f "${DEST}/default.sig" ]] || { print -u2 "default.sig を取得できませんでした"; exit 1 }

# ------------------------------------------------------------------- ClamAV

# Homebrew ではなく公式 .pkg を使う。理由:
#   - バージョンを固定でき、取得が再現可能（Homebrew は最新版しか入らない）
#   - universal (x86_64 + arm64)。Homebrew はホストのアーキテクチャのみ
#   - 依存が @rpath 参照のみで完結しており、openssl 等が静的リンク済み。
#     Homebrew 版は /opt/homebrew を指す絶対パスで libssl/libcrypto/libpcre2/
#     libjson-c を引くので、同梱にはそれら全ての再配置が要る
print "[3/6] ClamAV ${CLAMAV_VERSION} (universal)"
curl -fsSL -o "${WORK}/clamav.pkg" \
  "https://github.com/Cisco-Talos/clamav/releases/download/clamav-${CLAMAV_VERSION}/clamav-${CLAMAV_VERSION}.macos.universal.pkg"
pkgutil --expand-full "${WORK}/clamav.pkg" "${WORK}/clamav" >/dev/null

# pkg は programs / libraries / documentation に分かれており、bin ディレクトリは
# 複数の sub-package に現れる（中身が空のものもある）。ディレクトリ名で選ぶと
# 空の方を掴むので、実体があるかどうかで選ぶ。
payload_bin=$(dirname "$(find "${WORK}/clamav" -type f -path '*/usr/local/clamav/bin/clamscan' | head -1)")
payload_lib=$(dirname "$(find "${WORK}/clamav" -type f -path '*/usr/local/clamav/lib/libclamav.*.dylib' | head -1)")
[[ -f "${payload_bin}/clamscan" && -d "${payload_lib}" ]] || {
  print -u2 "ClamAV の payload 構成が想定と違います"; exit 1
}

# clamscan（検査）と freshclam（定義 DB の取得/更新）だけ。clamd 系は使わない。
for tool in clamscan freshclam; do
  cp -f "${payload_bin}/${tool}" "${DEST}/${tool}"
  chmod 755 "${DEST}/${tool}"
done

# CVD（定義 DB）の署名検証に使う root CA。ClamAV 1.4 以降、これが無いと
# freshclam は起動時点で失敗する:
#   Invalid certs directory '/usr/local/clamav/etc/certs/': No such file or directory
# 探索先はビルド時に焼き込まれた絶対パスなので、同梱して --cvdcertsdir で
# 明示的に渡す必要がある（siegfried の default.sig と同じ性質の落とし穴）。
cert_src=$(dirname "$(find "${WORK}/clamav" -type f -path '*/usr/local/clamav/etc/certs/*.crt' | head -1)")
[[ -d "${cert_src}" ]] || { print -u2 "ClamAV の証明書が見つかりません"; exit 1 }
mkdir -p "${DEST}/clamav-certs"
cp -f "${cert_src}"/*.crt "${DEST}/clamav-certs/"

# dylib は実体ファイルだけを、install id が示す名前（soname）で置く。
# 例: 実体 libclamav.12.1.0.dylib / id @rpath/libclamav.12.dylib → libclamav.12.dylib
# シンボリックリンクは張らない。バンドル内の symlink は Gatekeeper の
# 「invalid destination for symbolic link in bundle」を招きやすいため。
typeset -i libs=0
for real in "${payload_lib}"/*.dylib; do
  [[ -f "${real}" && ! -L "${real}" ]] || continue
  soname=$(otool -D "${real}" | tail -1)
  soname="${soname:t}"
  cp -f "${real}" "${DEST}/${soname}"
  chmod 755 "${DEST}/${soname}"
  # (( libs++ )) は後置なので値 0 → 終了ステータス 1 になり、set -e に殺される。
  libs=$(( libs + 1 ))
done
(( libs > 0 )) || { print -u2 "dylib を 1 つも取り出せませんでした"; exit 1 }

# 参照の付け替え。実体はすべて同じディレクトリに並べるので、
# 実行ファイルからは @executable_path、dylib からは @loader_path で足りる。
print "[4/6] dylib 参照を同梱先へ付け替え"
OLD_RPATH="/usr/local/clamav/lib"
for tool in clamscan freshclam; do
  install_name_tool -rpath "${OLD_RPATH}" "@executable_path" "${DEST}/${tool}" 2>/dev/null
done
for lib in "${DEST}"/*.dylib; do
  install_name_tool -rpath "${OLD_RPATH}" "@loader_path" "${lib}" 2>/dev/null
done

# install_name_tool は既存の署名を壊す。arm64 では署名が無効な実行ファイルは
# 起動時に SIGKILL されるので（rc=137。エラーメッセージも出ない）、ここで
# ad-hoc 署名を付け直す。配布時は sign.zsh が Developer ID で上書きする。
for f in "${DEST}"/*.dylib "${DEST}/clamscan" "${DEST}/freshclam"; do
  codesign --force --sign - "${f}" 2>/dev/null
done

# 付け替え漏れの検出。ここを飛ばすと、開発機では /usr/local/clamav が
# 無いので即座に落ちるが、ClamAV をインストール済みの機械では
# 「システム側の版で動いてしまう」ため気づけない。
leaked=0
for f in "${DEST}"/clamscan "${DEST}"/freshclam "${DEST}"/*.dylib; do
  if otool -l "${f}" | grep -q "${OLD_RPATH}"; then
    print -u2 "  付け替え漏れ: ${f}"
    leaked=1
  fi
done
(( leaked == 0 )) || exit 1

# 実際に起動するところまで確かめる。参照が解決できていなければここで落ちる。
if ! version=$("${DEST}/clamscan" --version 2>&1); then
  print -u2 "同梱した clamscan が起動しません: ${version}"
  exit 1
fi
print "  ${version}"

# freshclam は設定ファイルが無いと起動時点で落ちる。アプリ側は最小構成の
# freshclam.conf を自前で生成して渡すので、ここでも同じ形で起動を確かめる。
printf 'DatabaseDirectory %s\nDatabaseMirror database.clamav.net\n' "${WORK}/db" > "${WORK}/freshclam.conf"
mkdir -p "${WORK}/db"
if ! out=$("${DEST}/freshclam" --config-file="${WORK}/freshclam.conf" --datadir="${WORK}/db" \
             --cvdcertsdir="${DEST}/clamav-certs" --version 2>&1); then
  print -u2 "同梱した freshclam が起動しません: ${out}"
  exit 1
fi
print "  ${out}"

# 証明書の実体確認。--version は証明書を読まないので、ここを通っても
# 定義取得が成功する保証にはならない（実際の取得はアプリの設定画面から）。
ls "${DEST}/clamav-certs"/*.crt >/dev/null 2>&1 || {
  print -u2 "CVD 検証用の証明書が入っていません: ${DEST}/clamav-certs"
  exit 1
}

# --------------------------------------------------------- JHOVE + Java

check_sha256() {
  local file=$1 expected=$2 actual
  actual=$(shasum -a 256 "${file}" | cut -d' ' -f1)
  [[ "${actual}" == "${expected}" ]] || {
    print -u2 "ハッシュが一致しません: ${file:t}（期待 ${expected} / 実際 ${actual}）"
    exit 1
  }
}

print "[5/6] Java 実行環境 (Temurin ${TEMURIN_VERSION}, jlink)"
# 配布されている mac 版の JDK は arm64 と x64 で別。sf と同じく arm64 を入れる。
# Intel Mac では起動せず、アプリは「形式の適合性は未検査」と記録して続ける。
temurin_file="OpenJDK21U-jdk_aarch64_mac_hotspot_${TEMURIN_VERSION/+/_}.tar.gz"
curl -fsSL -o "${WORK}/jdk.tar.gz" \
  "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-${TEMURIN_VERSION/+/%2B}/${temurin_file}"
check_sha256 "${WORK}/jdk.tar.gz" "${TEMURIN_SHA256}"
tar -xzf "${WORK}/jdk.tar.gz" -C "${WORK}"
jdk_home=$(print "${WORK}"/jdk-*/Contents/Home)
rm -rf "${DEST}/jre"
"${jdk_home}/bin/jlink" --add-modules "${JRE_MODULES}" \
  --strip-debug --no-man-pages --no-header-files --compress=zip-6 \
  --output "${DEST}/jre"
# keytool は使わない。署名・公証の対象を 1 つ減らす。
rm -f "${DEST}/jre/bin/keytool"
# Temurin のライセンス表示（GPLv2 + Classpath Exception）は jre/legal/ に残る。
print "  $(du -sh "${DEST}/jre" | cut -f1)"

print "[6/6] JHOVE"
rm -rf "${DEST}/jhove"
mkdir -p "${DEST}/jhove"
for entry in "${JHOVE_JARS[@]}"; do
  jar_path=${entry%% *}
  curl -fsSL -o "${DEST}/jhove/${jar_path:t}" \
    "https://repo1.maven.org/maven2/org/openpreservation/jhove/${jar_path}"
  check_sha256 "${DEST}/jhove/${jar_path:t}" "${entry##* }"
done

# 実際に検査できるところまで確かめる。モジュールが足りなければここで落ちる
# （jlink で絞りすぎると、起動はしても検査の途中で ClassNotFound になる）。
# 設定ファイルが無いと JHOVE は ~/jhove/conf/ を探して落ちるので、アプリと
# 同じ最小構成（core/jhove.py の _CONFIG）を置く。検査する物は何でもよい。
cat > "${WORK}/jhove.conf" <<'CONF'
<?xml version="1.0" encoding="UTF-8"?>
<jhoveConfig version="1.0" xmlns="http://hul.harvard.edu/ois/xml/ns/jhove/jhoveConfig" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://hul.harvard.edu/ois/xml/ns/jhove/jhoveConfig http://hul.harvard.edu/ois/xml/xsd/jhove/1.4/jhoveConfig.xsd">
 <jhoveHome>.</jhoveHome>
 <module><class>edu.harvard.hul.ois.jhove.module.TiffModule</class></module>
 <module><class>edu.harvard.hul.ois.jhove.module.PdfModule</class></module>
</jhoveConfig>
CONF
# 絶対パスは cd の前に求める。サブシェルの中で ${DEST:A} を書くと、
# cd した後の ${WORK} を基準に解決されて jar が見つからない。
dest_abs=${DEST:A}
(cd "${WORK}" && "${dest_abs}/jre/bin/java" -Djava.awt.headless=true \
  -cp "$(print -l "${dest_abs}"/jhove/*.jar | paste -sd: -)" \
  edu.harvard.hul.ois.jhove.Jhove -c jhove.conf -h xml -m TIFF-hul jhove.conf \
  > "${WORK}/jhove.xml" 2>/dev/null) || true
grep -q 'name="Jhove"' "${WORK}/jhove.xml" || {
  print -u2 "同梱した JHOVE が動きません"
  exit 1
}
print "  $(grep -o 'release="[^"]*"' "${WORK}/jhove.xml" | head -1)"

print
print "取得しました:"
ls -la "${DEST}"
print
print "ウイルス定義 DB は同梱しません（巨大かつすぐ陳腐化する）。"
print "アプリの設定画面から freshclam で取得します。"
