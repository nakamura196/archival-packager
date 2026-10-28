#!/usr/bin/env zsh
# 配布用の .app / .exe を作る。
#
# 使い方:
#   ./scripts/build.zsh            # ホスト OS 向け
#   ./scripts/build.zsh windows    # Windows（Windows 上でのみ可。クロスビルド不可）
#
# 前提:
#   - 同梱バイナリを binaries/<os>/ に置いてあること（scripts/fetch-binaries.zsh）
#   - macOS では pod が flet の PATH から見えること（spike/README.md の落とし穴を参照）

set -euo pipefail

TARGET="${1:-macos}"
PRODUCT="Archival Packager"
BUNDLE_ID="com.nakamura.archivalpackager.x"

# --exclude が無いと .venv(145MB)・binaries・tests・spike まで app に入る。
# .venv は site-packages と重複しており、そのぶん丸ごと無駄。
# binaries は app.zip ではなくバンドル内 Resources/bin へ置くので除外する
# （app.zip に入れると公証で弾かれる。spike/README.md 参照）。
EXCLUDES=(
  .venv
  binaries
  build
  tests
  scripts
  spike
  .git
  .github
  .pytest_cache
  .ruff_cache
)

# macOS は最小 OS を 12.0 に上げた雛形を使う（Xcode 27 は 12.0 未満を受け付けない）。
# 理由と直す箇所は scripts/patch_flet_template.py の冒頭に書いた。
TEMPLATE_ARGS=()
if [[ "${TARGET}" == "macos" ]]; then
  TEMPLATE_ARGS=(--template "$(uv run python scripts/patch_flet_template.py)")
fi

print "[1/2] flet build ${TARGET}"
# --yes: Flutter SDK 導入の対話確認を飛ばす（無いと CI で EOFError）
# --no-rich-output: Windows のコンソールが進捗のスピナー文字を encode できない
uv run flet build "${TARGET}" . \
  --yes \
  --no-rich-output \
  --exclude "${EXCLUDES[@]}" \
  --product "${PRODUCT}" \
  --bundle-id "${BUNDLE_ID}" \
  --org com.nakamura \
  "${TEMPLATE_ARGS[@]}"

print "[2/2] 成果物"
if [[ "${TARGET}" == "macos" ]]; then
  APP=$(find build/macos -maxdepth 1 -name "*.app" | head -1)
  print "  ${APP} ($(du -sh "${APP}" | cut -f1))"
  print
  print "  次: ./scripts/sign.zsh で Developer ID 署名"
else
  print "  build/${TARGET}/ ($(du -sh "build/${TARGET}" | cut -f1))"
fi
