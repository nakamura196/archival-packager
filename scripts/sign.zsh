#!/usr/bin/env zsh
# Flet が生成した .app に同梱バイナリを埋め込み、Developer ID + hardened runtime で署名する。
#
# 使い方: ./spike/sign.zsh [.app へのパス]
#
# なぜ app.zip ではなくバンドル内に置くのか
# ------------------------------------------
# 当初は siegfried(sf) を Python アプリ側（assets/bin/）に置いていた。これは
# flet build により app.zip へ格納され、実行時に Application Support へ展開されて
# 正常に起動する。しかし公証に出すと弾かれた。Apple の審査は app.zip の中まで
# 降りて検査し、次の 3 点を要求する。
#
#   The binary is not signed with a valid Developer ID certificate.
#   The signature does not include a secure timestamp.
#   The executable does not have the hardened runtime enabled.
#
# zip 内のデータは署名できないため、バイナリは .app のバンドル内
# (Contents/Resources/bin/) に置き、個別に署名する必要がある。
# これは現行 aip（Swift 版）が Resources/bin/ に置いているのと同じ構成。
#
# 署名の取りこぼしに注意
# ----------------------
# 「実行ファイル」だけを署名対象にすると dylib を取りこぼす。実際に
# Python.framework の libssl.3.dylib / libcrypto.3.dylib は mode rw-r--r--
# （実行ビット無し）で、実行ビットで絞ると漏れて公証が Invalid になった。
# 判定は必ず file(1) の Mach-O 判定で行い、パーミッションで絞らないこと。

set -euo pipefail

# .app の名前は Flet のバージョンによって変わるため探索して決める
# （0.28 系は --product が反映されて "Flet Spike.app"、0.86 系は "spike.app"）。
APP="${1:-$(find build/macos -maxdepth 1 -name "*.app" 2>/dev/null | head -1)}"
IDENTITY="Developer ID Application: Satoru Nakamura (Q6S8JS6GWV)"
ENTITLEMENTS="scripts/entitlements.plist"
BINARIES="binaries/macos"

[[ -d "$APP" ]] || { print -u2 "アプリが見つかりません: $APP"; exit 1 }
[[ -f "$ENTITLEMENTS" ]] || { print -u2 "entitlements が見つかりません: $ENTITLEMENTS"; exit 1 }
for required in sf default.sig clamscan freshclam; do
  [[ -e "$BINARIES/$required" ]] || {
    print -u2 "同梱バイナリがありません: $BINARIES/$required（./scripts/fetch-binaries.zsh を先に）"
    exit 1
  }
done

# codesign のセキュアタイムスタンプ取得はネットワーク次第で確率的に失敗するため
# リトライで吸収する（現行 aip の export-devid.sh と同じ方針）。
retry() {
  local n=1
  until "$@"; do
    (( n++ ))
    (( n > 3 )) && return 1
    print -u2 "  retry ($n/3)"
    sleep 15
  done
}

print "[1/4] 同梱バイナリをバンドルへ配置"
DEST="$APP/Contents/Resources/bin"
mkdir -p "$DEST"
# binaries/macos/ の中身をそのまま入れる。個別に列挙すると、後から増えた
# ファイル（ClamAV の dylib 群など）を取りこぼす。
#
# 内訳:
#   sf, default.sig          siegfried 本体と署名 DB。default.sig を同梱しないと
#                            配布先で署名 DB を見つけられない（開発機では
#                            Homebrew 版を拾ってしまい、この欠陥に気づけない）
#   clamscan, freshclam      ウイルス検査と定義 DB の取得/更新
#   lib*.dylib               上記が @executable_path / @loader_path で引く実体
cp -R "$BINARIES"/. "$DEST"/
find "$DEST" -type f -name '*.dylib' -exec chmod 755 {} +
chmod 755 "$DEST/sf" "$DEST/clamscan" "$DEST/freshclam"
print "  $DEST ($(ls "$DEST" | wc -l | tr -d ' ') 件)"

# ライセンス表示を配布物に入れる。同梱している ClamAV は GPL-2.0 なので、
# 表示とソース入手手段の提示が要る。バンドルに入れずに配ると条件を満たさない。
for doc in LICENSE NOTICE; do
  [[ -f "$doc" ]] || { print -u2 "$doc がありません。配布物に必要です"; exit 1 }
  cp "$doc" "$APP/Contents/Resources/$doc"
done
print "  $APP/Contents/Resources/{LICENSE,NOTICE}"

# バンドル外を指す symlink があると、公証は通っても Gatekeeper が
#   rejected (invalid destination for symbolic link in bundle)
# で弾く。serious_python_darwin.framework の中に、ビルドしたマシンの
# pub-cache を指す絶対パス symlink (.pod) が残る。実行時には不要な残骸なので削除する。
# 署名の前に消すこと（後で消すと封が破れる）。
print "[1.5/4] バンドル外を指す symlink を除去"
typeset -i removed=0
while IFS= read -r link; do
  [[ -n "$link" ]] || continue
  print "  削除: ${link#$APP/} -> $(readlink "$link")"
  rm -f "$link"
  (( removed++ ))
done < <(find "$APP" -type l -exec sh -c 'case "$(readlink "$1")" in /*) echo "$1";; esac' _ {} \; 2>/dev/null)
if (( removed == 0 )); then print "  なし"; fi

print "[2/4] バンドル内の Mach-O を全て署名"
# パーミッションで絞らない（dylib は実行ビットを持たないことがある）。
#
# 署名は必ず深い階層から行う。framework 本体を先に署名してしまうと、
# 後からその内部の dylib を署名した時点で封が破れ、
# "a sealed resource is missing or invalid" になる。
# find の出力順は不定なので、パス区切りの数で深い順に並べ替える。
typeset -a machos
while IFS= read -r f; do
  [[ -n "$f" ]] && machos+=("$f")
done < <(find "$APP" -type f -exec sh -c 'file -b "$1" | grep -q "Mach-O" && echo "$1"' _ {} \; 2>/dev/null \
         | awk -F/ '{print NF"\t"$0}' | sort -rn | cut -f2-)

print "  対象 ${#machos[@]} 件（深い順）"
for f in "${machos[@]}"; do
  # アプリ本体の実行ファイルは最後に entitlements 付きで署名するので飛ばす。
  [[ "$f" == "$APP/Contents/MacOS/"* ]] && continue
  retry codesign --force --options runtime --timestamp -s "$IDENTITY" "$f" 2>/dev/null
done

# framework は「バンドル」として署名し直す必要がある。内部ファイルを個別に
# 署名した後でここを通すことで、封を正しく結び直す。これも深い順。
print "  framework バンドルを再署名"
while IFS= read -r fw; do
  [[ -n "$fw" ]] || continue
  retry codesign --force --options runtime --timestamp -s "$IDENTITY" "$fw" 2>/dev/null
done < <(find "$APP" -type d -name "*.framework" 2>/dev/null \
         | awk -F/ '{print NF"\t"$0}' | sort -rn | cut -f2-)
print "  完了"

print "[3/4] アプリ本体を再シール（entitlements 付き）"
retry codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" -s "$IDENTITY" "$APP"

print "[4/4] 検証"
codesign --verify --strict "$APP" && print "  codesign --verify --strict: OK"
print "  --- 同梱 sf の署名 ---"
codesign -dv --verbose=2 "$DEST/sf" 2>&1 | grep -E "Authority=Developer ID|TeamIdentifier|flags|Timestamp" | sed 's/^/    /'
print "  --- アプリ本体 ---"
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Authority=Developer ID|TeamIdentifier|flags|Timestamp" | sed 's/^/    /'
print "  --- 未署名 Mach-O が残っていないか ---"
unsigned=0
for f in "${machos[@]}"; do
  codesign -v "$f" >/dev/null 2>&1 || { print "    UNSIGNED: $f"; unsigned=1 }
done
if (( unsigned == 0 )); then print "    なし"; fi
