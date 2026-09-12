#!/usr/bin/env zsh
#
# ストア申請 API の資格情報を 1Password に入れる。
#
# 前提:
#   - store/API設定手順.md の 1〜3 を終えていること
#     （Entra のテナント作成 → アプリ登録 → Partner Center でマネージャー権限）
#   - op コマンドが使えること（Touch ID 連携済み）
#
# 使い方:
#   zsh scripts/setup/store-api-credentials.zsh
#
# 何をするか:
#   テナント ID・クライアント ID・キーを**画面に出さずに**受け取り、
#   1Password の項目 microsoft-store-api を作る。値はディスクに書かない。
#   入れ違い（テナント ID とクライアント ID の取り違え）はその場で弾く。
#
# 注意:
#   キーは Partner Center の画面を閉じると二度と見られない。
#   閉じてしまったら、Remove して新しいキーを作り直す。

set -euo pipefail
setopt interactive_comments

vault="Personal"
title="microsoft-store-api"
store_id="9N6XJD7THHPZ"
guid='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'

if op item get "$title" --vault "$vault" >/dev/null 2>&1; then
  print "既に $title があります。上書きせず、編集で入れ直します。"
  mode="edit"
else
  mode="create"
fi

read 'TENANT?ディレクトリ (テナント) ID: '
read 'CLIENT?アプリケーション (クライアント) ID: '
read -s 'SECRET?キーの値（表示されません）: '
print
read 'EXPIRES?キーの期限 (例 2028-09-12): '

fail() {
  print -u2 "中止: $1"
  unset TENANT CLIENT SECRET EXPIRES
  exit 1
}

[[ "$TENANT" =~ $guid ]] || fail "テナント ID が GUID の形ではありません。"
[[ "$CLIENT" =~ $guid ]] || fail "クライアント ID が GUID の形ではありません。"
[[ "$TENANT" != "$CLIENT" ]] || fail "テナント ID とクライアント ID が同じ値です。貼り間違いです。"
[[ -n "$SECRET" ]] || fail "キーが空です。"
[[ "$EXPIRES" =~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' ]] || fail "期限は YYYY-MM-DD で入れてください。"

if [[ "$mode" == "create" ]]; then
  op item create --category="API Credential" --vault="$vault" --title="$title" \
    "tenant_id[text]=$TENANT" "client_id[text]=$CLIENT" \
    "client_secret[concealed]=$SECRET" "store_id[text]=$store_id" \
    "secret_expires[text]=$EXPIRES" >/dev/null
else
  op item edit "$title" --vault="$vault" \
    "tenant_id[text]=$TENANT" "client_id[text]=$CLIENT" \
    "client_secret[concealed]=$SECRET" "store_id[text]=$store_id" \
    "secret_expires[text]=$EXPIRES" >/dev/null
fi

secret_len=${#SECRET}
unset TENANT CLIENT SECRET EXPIRES

print "1Password に $title を用意しました。"
print "  tenant_id     : $(op item get "$title" --vault "$vault" --fields tenant_id)"
print "  client_id     : $(op item get "$title" --vault "$vault" --fields client_id)"
print "  client_secret : 設定あり（${secret_len}文字・表示しません）"
print "  secret_expires: $(op item get "$title" --vault "$vault" --fields secret_expires)"
print
print "確認: op run --env-file=store/.env -- python scripts/store_submit.py --dry-run"
