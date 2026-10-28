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
#   veraPDF      … いまの規則が作る PDF は PDF/A ではない。PDF の検査は JHOVE で行う
#   ImageMagick  … 不要。画像は Pillow でアプリ内変換する

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SiegfriedVersion = "1-11-6"
$ClamAVVersion    = "1.5.3"

# JHOVE（変換した派生物の形式検査）と、それを動かす Java。
# macOS 側と同じ版・同じモジュール構成にする。ハッシュは取得元の差し替えに
# 気づくために固定している（理由は fetch-binaries.zsh の同じ箇所）。
$TemurinVersion = "21.0.12+8"
$TemurinSha256  = "9ba963ee2371874a74185d18bc7bb2ab9407df7683300855ed7606e0662321d0"  # x64 windows jdk
$JreModules     = "java.base,java.desktop,java.logging"
$JhoveJars = @(
    @{ Path = "jhove-apps/1.34.0/jhove-apps-1.34.0.jar";    Sha256 = "35dd738db51befe430ce48e3567f7f70a99ed1f14834fa23962b8e790cc9881b" },
    @{ Path = "jhove-core/1.34.0/jhove-core-1.34.0.jar";    Sha256 = "337a0a14c335272335f41f375118a51f7e025d5b016c5ee59e7869043d361dd0" },
    @{ Path = "modules/tiff-hul/1.9.5/tiff-hul-1.9.5.jar";  Sha256 = "c0ce300c28db5553bbc706a5190e433adf5b7c12a478795b01b3f38747ea0170" },
    @{ Path = "modules/pdf-hul/1.12.8/pdf-hul-1.12.8.jar";  Sha256 = "c5acf4e2c84aec6575390bcc43d9e015c6237b75eec5f1f354de7102e6e023bb" }
)

function Assert-Sha256([string]$File, [string]$Expected) {
    $actual = (Get-FileHash -Algorithm SHA256 -Path $File).Hash.ToLower()
    if ($actual -ne $Expected) {
        throw "ハッシュが一致しません: $File（期待 $Expected / 実際 $actual）"
    }
}

$Dest = "binaries\windows"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("fetch-" + [guid]::NewGuid())
New-Item -ItemType Directory -Force -Path $Work | Out-Null

try {
    $sfVer = $SiegfriedVersion.Replace("-", ".")

    Write-Host "[1/5] siegfried $sfVer"
    $sfZip = Join-Path $Work "sf.zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $sfZip `
        "https://github.com/richardlehane/siegfried/releases/download/v$sfVer/siegfried_${SiegfriedVersion}_win64.zip"
    Expand-Archive -Path $sfZip -DestinationPath (Join-Path $Work "sf") -Force
    # sf.exe だけ。roy.exe（署名 DB の作成ツール）は実行時に使わない。
    Copy-Item (Get-ChildItem -Recurse -Path (Join-Path $Work "sf") -Filter "sf.exe" | Select-Object -First 1).FullName `
        -Destination (Join-Path $Dest "sf.exe") -Force

    Write-Host "[2/5] siegfried 署名 DB (default.sig)"
    # default.sig が無いと配布先で署名 DB を見つけられない。
    # 開発機に siegfried が入っているとそちらを拾って動いてしまい、気づけない。
    $dataZip = Join-Path $Work "data.zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $dataZip `
        "https://github.com/richardlehane/siegfried/releases/download/v$sfVer/data_$SiegfriedVersion.zip"
    Expand-Archive -Path $dataZip -DestinationPath (Join-Path $Work "data") -Force
    $sig = Get-ChildItem -Recurse -Path (Join-Path $Work "data") -Filter "default.sig" | Select-Object -First 1
    if (-not $sig) { throw "default.sig を取得できませんでした" }
    Copy-Item $sig.FullName -Destination (Join-Path $Dest "default.sig") -Force

    Write-Host "[3/5] ClamAV $ClamAVVersion"
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

    Write-Host "[4/5] Java 実行環境 (Temurin $TemurinVersion, jlink)"
    $jdkZip = Join-Path $Work "jdk.zip"
    $tag = $TemurinVersion.Replace("+", "%2B")
    $file = "OpenJDK21U-jdk_x64_windows_hotspot_$($TemurinVersion.Replace('+', '_')).zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $jdkZip `
        "https://github.com/adoptium/temurin21-binaries/releases/download/jdk-$tag/$file"
    Assert-Sha256 $jdkZip $TemurinSha256
    Expand-Archive -Path $jdkZip -DestinationPath (Join-Path $Work "jdk") -Force
    $jdkHome = (Get-ChildItem -Directory -Path (Join-Path $Work "jdk") | Select-Object -First 1).FullName
    $jre = Join-Path $Dest "jre"
    if (Test-Path $jre) { Remove-Item -Recurse -Force $jre }
    & (Join-Path $jdkHome "bin\jlink.exe") --add-modules $JreModules `
        --strip-debug --no-man-pages --no-header-files --compress=zip-6 --output $jre
    if ($LASTEXITCODE -ne 0) { throw "jlink に失敗しました" }
    # keytool は使わない
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $jre "bin\keytool.exe")

    Write-Host "[5/5] JHOVE"
    $jhoveDir = Join-Path $Dest "jhove"
    if (Test-Path $jhoveDir) { Remove-Item -Recurse -Force $jhoveDir }
    New-Item -ItemType Directory -Force -Path $jhoveDir | Out-Null
    foreach ($jar in $JhoveJars) {
        $out = Join-Path $jhoveDir (Split-Path $jar.Path -Leaf)
        Invoke-WebRequest -UseBasicParsing -OutFile $out `
            "https://repo1.maven.org/maven2/org/openpreservation/jhove/$($jar.Path)"
        Assert-Sha256 $out $jar.Sha256
    }

    # 実際に検査できるところまで確かめる（設定ファイルはアプリと同じ最小構成）。
    $conf = Join-Path $Work "jhove.conf"
    Set-Content -Encoding utf8 -Path $conf -Value @'
<?xml version="1.0" encoding="UTF-8"?>
<jhoveConfig version="1.0" xmlns="http://hul.harvard.edu/ois/xml/ns/jhove/jhoveConfig" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://hul.harvard.edu/ois/xml/ns/jhove/jhoveConfig http://hul.harvard.edu/ois/xml/xsd/jhove/1.4/jhoveConfig.xsd">
 <jhoveHome>.</jhoveHome>
 <module><class>edu.harvard.hul.ois.jhove.module.TiffModule</class></module>
 <module><class>edu.harvard.hul.ois.jhove.module.PdfModule</class></module>
</jhoveConfig>
'@
    $cp = (Get-ChildItem -Path $jhoveDir -Filter "*.jar" | ForEach-Object { $_.FullName }) -join ";"
    # 作業ディレクトリを移す前に絶対パスにしておく（$jre は相対パス）。
    $java = Join-Path (Resolve-Path $jre).Path "bin\java.exe"
    Push-Location $Work
    try {
        $xml = & $java "-Djava.awt.headless=true" -cp $cp `
            edu.harvard.hul.ois.jhove.Jhove -c jhove.conf -h xml -m TIFF-hul jhove.conf 2>$null
    } finally { Pop-Location }
    if (-not ($xml -join "`n").Contains('name="Jhove"')) { throw "同梱した JHOVE が動きません" }
    Write-Host "  JHOVE を起動できました"

    Write-Host ""
    Write-Host "取得しました:"
    Get-ChildItem -Path $Dest | Format-Table Name, Length
    Write-Host "ウイルス定義 DB は同梱しません（巨大かつすぐ陳腐化する）。"
    Write-Host "アプリの設定画面から freshclam で取得します。"
}
finally {
    Remove-Item -Recurse -Force -Path $Work -ErrorAction SilentlyContinue
}
