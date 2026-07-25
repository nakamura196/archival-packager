# 同梱する外部バイナリを取得して binaries\windows\ に置く（Windows 用）。
#
# 使い方: pwsh -File scripts/fetch-binaries.ps1
#
# macOS 側は scripts/fetch-binaries.zsh。バージョンは両者で必ず揃えること。
# フォーマット識別やウイルス検査の結果が OS で変わると、同じ資料から作った
# 保存パッケージの再現性が崩れる。
#
# 同梱しないもの（理由は fetch-binaries.zsh 冒頭に詳しく書いてある）:
#   Ghostscript  … AGPL-3.0。同梱すると配布ライセンスの判断が要る
#   ImageMagick  … 不要。画像は Pillow でアプリ内変換する

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SiegfriedVersion = "1-11-6"
$ClamAVVersion    = "1.5.3"

$Dest = "binaries\windows"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("fetch-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $Work | Out-Null

try {
    $sfVer = $SiegfriedVersion.Replace("-", ".")

    Write-Host "[1/3] siegfried $sfVer"
    $sfZip = Join-Path $Work "sf.zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $sfZip `
        "https://github.com/richardlehane/siegfried/releases/download/v$sfVer/siegfried_${SiegfriedVersion}_win64.zip"
    Expand-Archive -Path $sfZip -DestinationPath (Join-Path $Work "sf") -Force
    # sf.exe だけ。roy.exe（署名 DB の作成ツール）は実行時に使わない。
    Copy-Item (Get-ChildItem -Recurse -Path (Join-Path $Work "sf") -Filter "sf.exe" | Select-Object -First 1).FullName `
        -Destination (Join-Path $Dest "sf.exe") -Force

    Write-Host "[2/3] siegfried 署名 DB (default.sig)"
    # default.sig が無いと配布先で署名 DB を見つけられない。
    # 開発機に siegfried が入っているとそちらを拾って動いてしまい、気づけない。
    $dataZip = Join-Path $Work "data.zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $dataZip `
        "https://github.com/richardlehane/siegfried/releases/download/v$sfVer/data_$SiegfriedVersion.zip"
    Expand-Archive -Path $dataZip -DestinationPath (Join-Path $Work "data") -Force
    $sig = Get-ChildItem -Recurse -Path (Join-Path $Work "data") -Filter "default.sig" | Select-Object -First 1
    if (-not $sig) { throw "default.sig を取得できませんでした" }
    Copy-Item $sig.FullName -Destination (Join-Path $Dest "default.sig") -Force

    Write-Host "[3/3] ClamAV $ClamAVVersion"
    # 公式の portable zip。インストーラ(.msi)と違い展開するだけで使える。
    $clamZip = Join-Path $Work "clamav.zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $clamZip `
        "https://github.com/Cisco-Talos/clamav/releases/download/clamav-$ClamAVVersion/clamav-$ClamAVVersion.win.x64.zip"
    Expand-Archive -Path $clamZip -DestinationPath (Join-Path $Work "clamav") -Force
    $clamRoot = (Get-ChildItem -Directory -Path (Join-Path $Work "clamav") | Select-Object -First 1).FullName

    # clamscan（検査）/ freshclam（定義 DB の取得・更新）と、その依存 DLL。
    # clamd 系・.pdb（デバッグ情報。合計 120MB 超）・.lib は入れない。
    foreach ($exe in @("clamscan.exe", "freshclam.exe")) {
        Copy-Item (Join-Path $clamRoot $exe) -Destination (Join-Path $Dest $exe) -Force
    }
    Get-ChildItem -Path $clamRoot -Filter "*.dll" | ForEach-Object {
        Copy-Item $_.FullName -Destination (Join-Path $Dest $_.Name) -Force
    }

    # CVD（定義 DB）の署名検証に使う root CA。ClamAV 1.4 以降、これが無いと
    # freshclam は起動時点で失敗する。探索先の既定はビルド時に焼き込まれた
    # 絶対パスなので、同梱して CVD_CERTS_DIR で明示的に渡す必要がある
    # （siegfried の default.sig と同じ性質の落とし穴）。
    $certs = Join-Path $Dest "clamav-certs"
    New-Item -ItemType Directory -Force -Path $certs | Out-Null
    Get-ChildItem -Path (Join-Path $clamRoot "certs") -Filter "*.crt" | ForEach-Object {
        Copy-Item $_.FullName -Destination (Join-Path $certs $_.Name) -Force
    }
    if (-not (Get-ChildItem -Path $certs -Filter "*.crt")) {
        throw "CVD 検証用の証明書が入っていません: $certs"
    }

    # 実際に起動するところまで確かめる。DLL が足りなければここで落ちる。
    $version = & (Join-Path $Dest "clamscan.exe") --version
    if ($LASTEXITCODE -ne 0) { throw "同梱した clamscan が起動しません" }
    Write-Host "  $version"

    Write-Host ""
    Write-Host "取得しました:"
    Get-ChildItem -Path $Dest | Format-Table Name, Length
    Write-Host "ウイルス定義 DB は同梱しません（巨大かつすぐ陳腐化する）。"
    Write-Host "アプリの設定画面から freshclam で取得します。"
}
finally {
    Remove-Item -Recurse -Force -Path $Work -ErrorAction SilentlyContinue
}
