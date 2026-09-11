#!/usr/bin/env zsh
#
# CI が作った Windows 版を、そのまま Google Drive のマイドライブへ置く。
#
# 前提:
#   - gh と rclone が使えること
#   - rclone に gdrive: がある（**共有ドライブを向いているので team_drive= で外す**。
#     外さないと「学術資産アーカイブ」に入る。一度やった）
#
# 使い方:
#   zsh scripts/publish-test-build.zsh <run-id>
#
# 成果物はもともと zip なので、展開して固め直さない。CI が作ったものを
# そのまま渡せるうえ、展開時の不具合も起きない。

set -euo pipefail

run_id="${1:?run-id を渡してください（gh run list で調べる）}"
repo="nakamura196/archival-packager-x"
version="$(grep -m1 '^version = ' pyproject.toml | sed 's/.*"\(.*\)"/\1/')"
name="ArchivalPackager-${version}-windows-x64.zip"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "== 成果物を取得 (run ${run_id}) =="
artifact_id="$(gh api "repos/${repo}/actions/runs/${run_id}/artifacts" \
  --jq '.artifacts[] | select(.name=="archival-packager-windows-unsigned") | .id')"
[[ -n "$artifact_id" ]] || { echo "成果物が見つかりません"; exit 1; }

gh api "repos/${repo}/actions/artifacts/${artifact_id}/zip" > "${work}/${name}"

echo "== 中身を確かめる =="
for f in archival-packager.exe bin/sf.exe bin/clamscan.exe NOTICE LICENSE; do
  unzip -l "${work}/${name}" "$f" >/dev/null 2>&1 || { echo "  **${f} が無い**"; exit 1; }
  echo "  ${f} あり"
done
size="$(stat -f%z "${work}/${name}")"
echo "  サイズ: $((size / 1024 / 1024)) MB"

echo "== マイドライブへ =="
rclone copy "${work}/${name}" "gdrive,team_drive=:ArchivalPackager/" --stats-one-line

echo
echo "置いた: マイドライブ / ArchivalPackager / ${name}"
shasum -a 256 "${work}/${name}" | awk '{print "SHA-256: " $1}'
echo
echo "Windows 側での注意: 展開する前に zip を右クリック →"
echo "プロパティ → 「許可する」にチェック。付けないと実行が止められることがある。"
