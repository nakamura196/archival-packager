#!/usr/bin/env zsh
#
# 署名済みの .app を、そのままの形で自己診断にかける。
#
# なぜ要るか: Python 側のテストは Flutter 側の部品まで届かない。
# 「フォルダを選ぶ」は、署名に entitlement が入っていないと
# ダイアログを開く前に落ちる（0.1.5 で踏んだ）。
# ビルドも起動も通るので、実物を動かして初めて分かる。
#
# 使い方: zsh scripts/self-test-mac.zsh [.app へのパス]

set -euo pipefail

app="${1:-build/macos/archival-packager.app}"
[[ -d "$app" ]] || { print -u2 "見つかりません: $app"; exit 1 }

bin="$app/Contents/MacOS/$(basename "$app" .app)"
[[ -x "$bin" ]] || bin="$(find "$app/Contents/MacOS" -type f -perm -111 | head -1)"

print "自己診断: $bin"
log="$(mktemp)"
"$bin" --self-test > "$log" 2>&1 &
pid=$!

i=0
while [ $i -lt 24 ] && kill -0 $pid 2>/dev/null; do
  sleep 5
  i=$((i+1))
done

if kill -0 $pid 2>/dev/null; then
  kill -9 $pid 2>/dev/null || true
  print -u2 "2 分で終わりませんでした"
  cat "$log"
  exit 1
fi

wait $pid
status=$?
cat "$log"
rm -f "$log"

if [ $status -ne 0 ]; then
  print -u2 "自己診断に落ちた項目があります（終了コード $status）"
  exit 1
fi
print "自己診断: すべて通りました"
