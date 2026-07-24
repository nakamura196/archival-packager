#!/usr/bin/env zsh
# Flet が生成した .app を Developer ID + hardened runtime で署名する（検証用）。
#
# 前提: Developer ID Application 証明書がキーチェーンにあること。
# 使い方: ./spike/sign.zsh [.app へのパス]
#
# 何をするか:
#   1. .app 内の Mach-O を内側から順に署名する（Python stdlib の .so を含む）
#   2. entitlements 付きでアプリ本体を再シールする
#   3. 検証して結果を表示する
#
# 署名は内側から先に行う必要がある。外側を先に署名すると、内側を署名した時点で
# 外側の封が破れるため。--deep は Apple 非推奨なので使わない。
#
# 注: siegfried(sf) は app.zip 内のデータとして入るため、ここでは署名対象にならない。
# 実行時に Application Support へ展開され、元の ad-hoc 署名のまま起動する。
# 公証がこれを許容するかが本検証の焦点。

set -euo pipefail

APP="${1:-spike/build/macos/Flet Spike.app}"
IDENTITY="Developer ID Application: Satoru Nakamura (Q6S8JS6GWV)"
ENTITLEMENTS="spike/entitlements.plist"

[[ -d "$APP" ]] || { print -u2 "アプリが見つかりません: $APP"; exit 1 }
[[ -f "$ENTITLEMENTS" ]] || { print -u2 "entitlements が見つかりません: $ENTITLEMENTS"; exit 1 }

# codesign のセキュアタイムスタンプ取得はネットワーク次第で確率的に失敗するため
# リトライで吸収する（現行 aip の export-devid.sh と同じ方針）。
retry() {
  local n=1
  until "$@"; do
    (( n++ ))
    (( n > 3 )) && return 1
    print -u2 "  retry ($n/3): $1"
    sleep 15
  done
}

print "[1/3] 内部 Mach-O を署名"
typeset -a machos
while IFS= read -r f; do
  [[ -n "$f" ]] && machos+=("$f")
done < <(find "$APP" -type f -perm +111 -exec sh -c 'file -b "$1" | grep -q Mach-O && echo "$1"' _ {} \; 2>/dev/null)

print "  対象 ${#machos[@]} 件"
for f in "${machos[@]}"; do
  # アプリ本体の実行ファイルは最後に entitlements 付きで署名するので飛ばす。
  [[ "$f" == "$APP/Contents/MacOS/"* ]] && continue
  retry codesign --force --options runtime --timestamp -s "$IDENTITY" "$f" 2>/dev/null
done
print "  完了"

print "[2/3] アプリ本体を再シール（entitlements 付き）"
retry codesign --force --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" -s "$IDENTITY" "$APP"

print "[3/3] 検証"
codesign --verify --strict --verbose=2 "$APP" 2>&1 | sed 's/^/  /'
print "  --- 署名情報 ---"
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Authority|TeamIdentifier|flags|Timestamp" | sed 's/^/  /'
print "  --- Gatekeeper 評価（公証前なので rejected が正常）---"
spctl -a -vv "$APP" 2>&1 | sed 's/^/  /' || true
