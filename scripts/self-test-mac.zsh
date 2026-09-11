#!/usr/bin/env zsh
#
# 署名済みの .app を、そのままの形で自己診断にかける。
#
# なぜ要るか: Python 側のテストは Flutter 側の部品まで届かない。
# 「フォルダを選ぶ」は、署名に entitlement が入っていないと
# ダイアログを開く前に落ちる（0.1.5 で踏んだ）。
# ビルドも起動も通るので、実物を動かして初めて分かる。
#
# 結果は**ファイル**で受け取る。包んだアプリの標準出力は、
# 呼び出し元まで戻ってこない。
#
# 使い方: zsh scripts/self-test-mac.zsh [.app へのパス]

set -euo pipefail

app="${1:-build/macos/archival-packager.app}"
[[ -d "$app" ]] || { print -u2 "見つかりません: $app"; exit 1 }

bin="$app/Contents/MacOS/$(basename "$app" .app)"
[[ -x "$bin" ]] || bin="$(find "$app/Contents/MacOS" -type f -perm -111 | head -1)"

# 結果は、アプリが記録（errors.log）を置くのと同じ場所に出る。
# 任意のパスを渡しても残らないことが何度もあったため（macOS の TMPDIR、
# CI の作業ディレクトリ）、アプリが確実に書ける場所に揃えた。
report="${XDG_DATA_HOME:-$HOME/.local/share}/ArchivalPackager/self-test.txt"
rm -f "$report"

# 前の回のアプリが残っていると、新しく起動しても何も起きない（実測）。
# 走らせる前に必ず片づける。
# 前の回のアプリが残っていると、新しく起動しても何も起きない（実測）。
#
# パターンは**自分自身に一致しない形**にする。pkill -f は呼び出し元シェルの
# コマンド行も見るので、素直に書くと自分やこのスクリプトを殺してしまう。
# 角かっこで 1 文字外すのは、ps | grep で昔から使われている手。
pkill -f "MacOS/archival-pack[a]ger" 2>/dev/null || true
# 片づけた直後に起動しても立ち上がらないことがある。少し待つ。
sleep 8

print "自己診断: $bin"
# 出力はファイルに流す。/dev/null に捨てると起動しなかった（実測）。
ARCHIVAL_PACKAGER_SELF_TEST="$report" "$bin" --self-test > /tmp/archival-packager-self-test.log 2>&1 &
pid=$!

# Python の展開に時間がかかる。結果ファイルが出るまで待つ。
i=0
while [ $i -lt 36 ] && kill -0 $pid 2>/dev/null; do
  if [[ -f "$report" ]] && grep -q "自己診断:" "$report"; then break; fi
  sleep 5
  i=$((i+1))
done

kill -9 $pid 2>/dev/null || true

if [[ ! -f "$report" ]]; then
  print -u2 "自己診断が結果を残しませんでした（起動していないか、書けていない）"
  exit 1
fi

cat "$report"
if ! grep -q "自己診断: PASS" "$report"; then
  print -u2 "自己診断に落ちた項目があります"
  rm -f "$report"
  exit 1
fi
rm -f "$report"
print "自己診断: すべて通りました"
