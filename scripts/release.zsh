#!/usr/bin/env zsh
# 公証済み .app を .dmg に固め、dmg 自体も署名・公証・staple する。
#
# 使い方:
#   op run --env-file=<aip>/.env -- ./scripts/release.zsh              # dmg を作るだけ
#   op run --env-file=<aip>/.env -- ./scripts/release.zsh --publish    # タグ付け + GitHub Release
#
# 前提:
#   - ./scripts/build.zsh → sign.zsh → notarize.zsh まで完了していること
#     （このスクリプトは .app が署名・公証・staple 済みであることを検証してから始める）
#   - create-dmg（brew install create-dmg）
#   - APP_STORE_API_KEY / APP_STORE_API_ISSUER（1Password 参照を op run で注入）
#
# なぜ .app を配らず .dmg にするか
# --------------------------------
# .app をそのまま zip で配ると、展開時に実行ビットや署名が壊れることがある。
# また Gatekeeper の隔離属性まわりの挙動が .dmg のほうが素直で、
# 「ドラッグして Applications へ」という導線も利用者に馴染みがある。
# 現行 Swift 版も notarized .dmg で配っており、形を揃える。
#
# なぜ dmg も公証するか
# ---------------------
# 中の .app を staple してあっても、dmg 自体に ticket が無いと、
# 初回マウント時にネットワーク越しの検証が要る。オフラインの端末や、
# 検疫の厳しい組織内ネットワークではそこで止まる。dmg にも staple しておく。
#
# --publish はしない限り外に何も出さない。既定は dmg を作るところまで。

set -euo pipefail

PUBLISH=""
[[ "${1:-}" == "--publish" ]] && PUBLISH=1

APP=$(find build/macos -maxdepth 1 -name "*.app" 2>/dev/null | head -1)
IDENTITY="Developer ID Application: Satoru Nakamura (Q6S8JS6GWV)"
VOLNAME="Archival Packager"

[[ -d "$APP" ]] || { print -u2 "アプリが見つかりません。./scripts/build.zsh を先に実行してください"; exit 1 }
command -v create-dmg >/dev/null || { print -u2 "create-dmg がありません（brew install create-dmg）"; exit 1 }
: "${APP_STORE_API_KEY:?APP_STORE_API_KEY が未設定です。op run 経由で実行してください}"
: "${APP_STORE_API_ISSUER:?APP_STORE_API_ISSUER が未設定です}"

KEY_PATH="$HOME/.private_keys/AuthKey_${APP_STORE_API_KEY}.p8"
[[ -f "$KEY_PATH" ]] || { print -u2 "API キーが見つかりません: $KEY_PATH"; exit 1 }

VERSION=$(grep -m1 -E '^version *= *"' pyproject.toml | sed -E 's/.*"([^"]+)".*/\1/')
[[ -n "$VERSION" ]] || { print -u2 "pyproject.toml から version を読めません"; exit 1 }
TAG="v${VERSION}"
DMG="build/archival-packager-${VERSION}.dmg"

print "リリース ${TAG}"

# --------------------------------------------------------------------------
print "[1/5] .app の状態を確認"

# ここを飛ばすと、未公証の .app を dmg に固めて配ってしまう。
# dmg 側の公証は通るので、配った後に利用者側で初めて弾かれる。
codesign --verify --strict "$APP" \
  || { print -u2 "  署名が不正です。./scripts/sign.zsh を先に"; exit 1 }
xcrun stapler validate "$APP" >/dev/null 2>&1 \
  || { print -u2 "  公証チケットがありません。./scripts/notarize.zsh を先に"; exit 1 }

# ライセンス表示の同梱漏れ。GPL-2.0 の ClamAV を入れている以上、必須。
for doc in LICENSE NOTICE; do
  [[ -f "$APP/Contents/Resources/$doc" ]] \
    || { print -u2 "  $doc がバンドルに入っていません。./scripts/sign.zsh を先に"; exit 1 }
done
print "  署名・公証・ライセンス表示 OK"

if [[ -n "$PUBLISH" ]]; then
  [[ -z "$(git status --porcelain --untracked-files=no)" ]] \
    || { print -u2 "  未コミットの変更があります"; exit 1 }
  git rev-parse "$TAG" >/dev/null 2>&1 \
    && { print -u2 "  タグ $TAG は既にあります。pyproject.toml の version を上げてください"; exit 1 }
fi

# --------------------------------------------------------------------------
print "[2/5] .dmg を作成"
rm -f "$DMG"
CREATE_DMG_ARGS=(
  --volname "$VOLNAME"
  --window-pos 200 120
  --window-size 600 400
  --icon-size 120
  --icon "${APP:t}" 150 200
  --hide-extension "${APP:t}"
  --app-drop-link 450 200
  --no-internet-enable
)
ICNS="$APP/Contents/Resources/AppIcon.icns"
[[ -f "$ICNS" ]] && CREATE_DMG_ARGS+=(--volicon "$ICNS")

create-dmg "${CREATE_DMG_ARGS[@]}" "$DMG" "$APP" >/dev/null
print "  $DMG ($(du -h "$DMG" | cut -f1))"

# --------------------------------------------------------------------------
print "[3/5] .dmg に署名"
# create-dmg の出力は未署名。未署名のまま公証に出しても通ることはあるが、
# 署名しておけば「配布中に差し替えられていない」ことまで検証できる。
codesign --force --timestamp -s "$IDENTITY" "$DMG"
codesign --verify --strict "$DMG"
print "  OK"

# --------------------------------------------------------------------------
print "[4/5] .dmg を公証（完了まで待機）"
xcrun notarytool submit "$DMG" \
  --key "$KEY_PATH" --key-id "$APP_STORE_API_KEY" --issuer "$APP_STORE_API_ISSUER" --wait

xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"

# --------------------------------------------------------------------------
print "[5/5] 配布前の最終確認"
# ダウンロード直後の利用者と同じ経路で確かめる。マウントした .app を
# 評価しないと、dmg だけ通って中身が弾かれる構成に気づけない。
MOUNT=$(mktemp -d)
hdiutil attach "$DMG" -nobrowse -quiet -mountpoint "$MOUNT"
trap 'hdiutil detach "$MOUNT" -quiet 2>/dev/null || true; rmdir "$MOUNT" 2>/dev/null || true' EXIT

MOUNTED_APP=$(find "$MOUNT" -maxdepth 1 -name "*.app" | head -1)
[[ -d "$MOUNTED_APP" ]] || { print -u2 "  dmg の中に .app がありません"; exit 1 }
spctl -a -vv "$MOUNTED_APP" 2>&1 | sed 's/^/  /'
"$MOUNTED_APP/Contents/Resources/bin/clamscan" --version | sed 's/^/  同梱 ClamAV: /'
"$MOUNTED_APP/Contents/Resources/bin/sf" -version | tail -1 | sed 's/^/  同梱 siegfried: /'

hdiutil detach "$MOUNT" -quiet
trap - EXIT
rmdir "$MOUNT" 2>/dev/null || true

if [[ -z "$PUBLISH" ]]; then
  print
  print "完了: $DMG"
  print "公開する場合は --publish を付けて再実行してください（タグ $TAG を打ちます）。"
  exit 0
fi

# --------------------------------------------------------------------------
print
print "タグ $TAG を打って GitHub Release を作成"
git tag -a "$TAG" -m "Archival Packager $TAG"
git push origin "$TAG"
gh release create "$TAG" "$DMG" --title "$TAG" --generate-notes --verify-tag
print "  $(gh release view "$TAG" --json url -q .url)"
