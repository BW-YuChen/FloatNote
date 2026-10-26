$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

# Build multi-size ICO: dark rounded square + white check + short underline.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $scriptDir) { $scriptDir = (Get-Location).Path }
$outFile = [System.IO.Path]::Combine($scriptDir, "..\Assets\app.ico")
$outFile = [System.IO.Path]::GetFullPath($outFile)
$dir = [System.IO.Path]::GetDirectoryName($outFile)
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

$sizes = @(16, 20, 24, 32, 48, 64, 128, 256)
$pngList = @()

foreach ($s in $sizes) {
    $bmp = New-Object System.Drawing.Bitmap($s, $s, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)

    $k = $s / 256.0
    $ink = [System.Drawing.Color]::FromArgb(255, 32, 32, 31)

    $r = [Math]::Max(2, 58 * $k)
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $d = $r * 2
    $w = $s
    $path.AddArc(0, 0, $d, $d, 180, 90)
    $path.AddArc($w - $d, 0, $d, $d, 270, 90)
    $path.AddArc($w - $d, $w - $d, $d, $d, 0, 90)
    $path.AddArc(0, $w - $d, $d, $d, 90, 90)
    $path.CloseFigure()
    $brush = New-Object System.Drawing.SolidBrush($ink)
    $g.FillPath($brush, $path)
    $brush.Dispose(); $path.Dispose()

    $paper = [System.Drawing.Color]::FromArgb(255, 250, 250, 247)

    if ($s -ge 32) {
        $penW = [Math]::Max(2.0, 26 * $k)
        $pen = New-Object System.Drawing.Pen($paper, $penW)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
        $pts = @(
            (New-Object System.Drawing.PointF([float](72 * $k), [float](126 * $k))),
            (New-Object System.Drawing.PointF([float](112 * $k), [float](166 * $k))),
            (New-Object System.Drawing.PointF([float](186 * $k), [float](86 * $k)))
        )
        $g.DrawLines($pen, $pts)
        $pen.Dispose()

        $pen2 = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(150, 250, 250, 247), [float]([Math]::Max(1.5, 16 * $k)))
        $pen2.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen2.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $g.DrawLine($pen2, [float](72 * $k), [float](206 * $k), [float](150 * $k), [float](206 * $k))
        $pen2.Dispose()
    }
    else {
        $pen = New-Object System.Drawing.Pen($paper, [float]([Math]::Max(1.6, 26 * $k * 1.15)))
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
        $pts = @(
            (New-Object System.Drawing.PointF([float](58 * $k), [float](134 * $k))),
            (New-Object System.Drawing.PointF([float](106 * $k), [float](182 * $k))),
            (New-Object System.Drawing.PointF([float](200 * $k), [float](74 * $k)))
        )
        $g.DrawLines($pen, $pts)
        $pen.Dispose()
    }

    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    $pngList += , @{ Size = $s; Bytes = $ms.ToArray() }
    $ms.Dispose()
}

$fs = [System.IO.File]::Create($outFile)
$bw = New-Object System.IO.BinaryWriter($fs)
$bw.Write([UInt16]0)
$bw.Write([UInt16]1)
$bw.Write([UInt16]$pngList.Count)

$offset = 6 + 16 * $pngList.Count
foreach ($p in $pngList) {
    $dim = if ($p.Size -ge 256) { 0 } else { $p.Size }
    $bw.Write([Byte]$dim)
    $bw.Write([Byte]$dim)
    $bw.Write([Byte]0)
    $bw.Write([Byte]0)
    $bw.Write([UInt16]1)
    $bw.Write([UInt16]32)
    $bw.Write([UInt32]$p.Bytes.Length)
    $bw.Write([UInt32]$offset)
    $offset += $p.Bytes.Length
}
foreach ($p in $pngList) { $bw.Write($p.Bytes) }
$bw.Flush(); $bw.Close(); $fs.Close()

Write-Output ("ICO written: " + $outFile + " (" + (Get-Item $outFile).Length + " bytes)")
