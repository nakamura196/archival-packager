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
#   テナント ID・クライアント ID・シークレットを**画面に出さずに**受け取り、
#   1Password の項目 microsoft-store-api を作る。値はディスクに書かない。
#
# 注意:
#   シークレットは Azure の画面を閉じると二度と見られない。
#   閉じてしまったら、新しいシークレットを作り直す。

set -euo pipefail
setopt interactive_comments

vault="Personal"
title="microsoft-store-api"

if op item get "$title" --vault "$vault" >/dev/null 2>&1; then
  print "既に $title があります。上書きせず、編集で入れ直します。"
  mode="edit"
else
  mode="create"
fi

read 'TENANT?ディレクトリ (テナント) ID: '
read 'CLIENT?アプリケーション (クライアント) ID: '
read -s 'SECRET?クライアント シークレットの値（表示されません）: '
print
read 'EXPIRES?シークレットの期限 (例 2028-09-12): '

store_id="9N6XJD7THHPZ"

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

unset TENANT CLIENT SECRET EXPIRES

print "1Password に $title を用意しました。"
print "確認: op run --env-file=store/.env -- python scripts/store_submit.py --dry-run"
