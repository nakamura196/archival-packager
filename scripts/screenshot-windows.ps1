# Store の掲載に使うスクリーンショットを、アプリを起動して撮る。
#
# 手元に Windows 機が無いので、CI の Windows マシンの上で撮る。
# 2026-09-08 に実際に撮れることを確認した。
#
# **画面全体ではなくアプリの窓だけを切り出す。** 全体を撮ると、後ろの端末画面と
# 「Test Mode / Windows Server 2025」の透かしが写り込み、掲載には使えない。
#
# 使い方: pwsh -File scripts/screenshot-windows.ps1
# 前提: build\windows にビルド済みの .exe があること。

# 起動の失敗は止める（ここが本体の検査）。撮影の失敗は警告にとどめる。
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")]
  public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int t, bool repaint);
  [DllImport("user32.dll")]
  public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("dwmapi.dll")]
  public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT r, int size);
}
"@

# Store は 1366x768 以上を求める。画面も窓もそれ以上にする。
try {
    Set-DisplayResolution -Width 1920 -Height 1080 -Force -ErrorAction Stop
    Write-Host "画面を 1920x1080 にした"
} catch {
    Write-Host "解像度を変更できなかった: $($_.Exception.Message)"
}

$exe = @(Get-ChildItem -Path build\windows -Filter "*.exe" -File)[0].FullName
Write-Host "起動: $exe"
$proc = Start-Process -FilePath $exe -PassThru

# Flet は初回起動で Python を展開するので時間がかかる。窓が出るまで待つ。
# 待たずに撮ると真っ黒な画像になる。
$handle = [IntPtr]::Zero
for ($i = 1; $i -le 60; $i++) {
    Start-Sleep -Seconds 2
    $proc.Refresh()
    if ($proc.MainWindowHandle -ne [IntPtr]::Zero) {
        $handle = $proc.MainWindowHandle
        Write-Host "窓が出るまで $($i * 2) 秒"
        break
    }
}
if ($handle -eq [IntPtr]::Zero) {
    throw "アプリの窓が出ませんでした。パッケージは作れているが起動していない"
}

# 起動直後に落ちていないか。窓が出たあとすぐ死ぬ場合がある。
Start-Sleep -Seconds 5
$proc.Refresh()
if ($proc.HasExited) {
    throw "起動直後にアプリが終了しました（終了コード $($proc.ExitCode)）"
}

# 窓の取っ手（ハンドル）は数秒で取れるが、Flet はそのあとで
# 自分の既定の大きさ（app.py の page.window.width/height = 1000x820）を当てる。
# すぐ広げても上書きされるので、落ち着くまで待ってから広げる。
Start-Sleep -Seconds 25

# Store は 1366x768 以上を求める。広げてから、実際に効いたか確かめる。
for ($try = 1; $try -le 3; $try++) {
    [void][Win32]::MoveWindow($handle, 0, 0, 1500, 980, $true)
    [void][Win32]::SetForegroundWindow($handle)
    Start-Sleep -Seconds 5
    $probe = New-Object Win32+RECT
    [void][Win32]::DwmGetWindowAttribute($handle, 9, [ref]$probe, 16)
    if (($probe.Right - $probe.Left) -ge 1366) { break }
    Write-Host "広げ直す（$try 回目。今 $($probe.Right - $probe.Left) px）"
}

# DWMWA_EXTENDED_FRAME_BOUNDS(9) を使う。GetWindowRect だと影の分の余白が入る。
$r = New-Object Win32+RECT
[void][Win32]::DwmGetWindowAttribute($handle, 9, [ref]$r, 16)
$w = $r.Right - $r.Left
$h = $r.Bottom - $r.Top
Write-Host "窓の大きさ: ${w}x${h}"
if ($w -lt 1366) { Write-Host "::warning::幅が 1366 未満。Store の要件を満たさない" }

New-Item -ItemType Directory -Force -Path screenshots | Out-Null
$out = "screenshots\01-sip.png"
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $h))
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()

# 単色に近ければ描画されていない。撮れたかどうかの判定に使う。
$img = [System.Drawing.Image]::FromFile((Resolve-Path $out))
$b = New-Object System.Drawing.Bitmap $img
$colors = @{}
for ($x = 0; $x -lt $b.Width; $x += 37) {
    for ($y = 0; $y -lt $b.Height; $y += 37) { $colors[$b.GetPixel($x, $y).ToArgb()] = $true }
}
Write-Host "色の種類: $($colors.Count)"
if ($colors.Count -le 2) { Write-Host "::warning::画面が描画されていない可能性が高い" }
$b.Dispose()
$img.Dispose()

if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
Write-Host "書き出し: $out"
