#!/usr/bin/env zsh
# 署名済み .app を Apple の notary service に送り、ticket を staple する（検証用）。
#
# 前提:
#   - spike/sign.zsh が完了していること（Developer ID + hardened runtime）
#   - App Store Connect API キーが ~/.private_keys/AuthKey_<KEY_ID>.p8 にあること
#   - APP_STORE_API_KEY / APP_STORE_API_ISSUER が環境にあること
#     （aip/app/.env は 1Password 参照なので op run 経由で渡す）
#
# 使い方:
#   op run --env-file=/Users/nakamura/git/kim/aip/app/.env -- ./spike/notarize.zsh
#
# この検証の焦点:
#   同梱した siegfried(sf) は app.zip 内のデータとして入り、Developer ID では
#   署名されていない（元の ad-hoc 署名のまま）。Apple の審査がこれを
#   「未署名の実行ファイルを含む」と判定して弾くかどうかを確かめる。
#   弾かれた場合は、sf を app.zip から出して .app 内に配置し個別署名する構成へ移す。

set -euo pipefail

APP="${1:-spike/build/macos/Flet Spike.app}"
ZIP="spike/build/FletSpike-notarize.zip"

[[ -d "$APP" ]] || { print -u2 "アプリが見つかりません: $APP"; exit 1 }
: "${APP_STORE_API_KEY:?APP_STORE_API_KEY が未設定です。op run 経由で実行してください}"
: "${APP_STORE_API_ISSUER:?APP_STORE_API_ISSUER が未設定です}"

KEY_PATH="$HOME/.private_keys/AuthKey_${APP_STORE_API_KEY}.p8"
[[ -f "$KEY_PATH" ]] || { print -u2 "API キーが見つかりません: $KEY_PATH"; exit 1 }

print "[1/4] 署名状態を確認"
codesign --verify --strict "$APP" || { print -u2 "署名が不正です。先に sign.zsh を実行してください"; exit 1 }
print "  OK"

# notarytool は .app ディレクトリを直接受け取れないため zip に固める。
# ditto --keepParent でバンドル構造とリソースフォークを保つ（zip コマンドでは壊れる）。
print "[2/4] 送信用 zip を作成"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
print "  $ZIP ($(du -h "$ZIP" | cut -f1))"

print "[3/4] notary service へ送信（完了まで待機）"
xcrun notarytool submit "$ZIP" \
  --key "$KEY_PATH" \
  --key-id "$APP_STORE_API_KEY" \
  --issuer "$APP_STORE_API_ISSUER" \
  --wait

print "[4/4] ticket を staple して Gatekeeper 評価"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
print "  --- spctl ---"
spctl -a -vv "$APP" 2>&1 | sed 's/^/  /'
