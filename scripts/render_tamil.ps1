# Rasterise shaped text lines to transparent PNGs using GDI+.
#
# This exists because ffmpeg's drawtext cannot shape Tamil on this machine and
# Pillow has no raqm, so both drop the ligating vowel signs (u, uu) and misplace
# the pre-base ones. GDI+ goes through Uniscribe and shapes correctly, and it is
# already on every Windows box, so nothing has to be installed.
#
# Takes one JSON job file and writes one JSON result file, so a whole build's
# captions cost a single process launch rather than one per line.
#
# Job: {id, text, font, size, color, max_width, max_lines, min_size, out_prefix}
# Result: {id, size, lines: [{file, w, h}]}

param([Parameter(Mandatory=$true)][string]$JobFile,
      [Parameter(Mandatory=$true)][string]$OutFile)

Add-Type -AssemblyName System.Drawing

$jobs = [IO.File]::ReadAllText($JobFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
$measure = New-Object System.Drawing.Bitmap 8,8
$mg = [System.Drawing.Graphics]::FromImage($measure)
$mg.TextRenderingHint = 'AntiAlias'
$fmt = [System.Drawing.StringFormat]::GenericTypographic
$fmt.FormatFlags = $fmt.FormatFlags -bor [System.Drawing.StringFormatFlags]::MeasureTrailingSpaces

function Get-Width([string]$s, $font) {
    return $mg.MeasureString($s, $font, [int]::MaxValue, $fmt).Width
}

# Wrap to at most maxLines, shrinking the point size until it fits. Mirrors what
# a caption has to do on screen: too wide is unreadable, too small is worse.
function Fit-Text([string]$text, [string]$fontName, [double]$size,
                  [double]$maxWidth, [int]$maxLines, [double]$minSize) {
    # @() matters: for single-word text -split returns a bare string, and
    # indexing a string in PowerShell yields a CHARACTER. Without it a one word
    # title renders as its first letter and nothing else, which looks like a
    # font problem rather than a bug.
    $words = @($text -split '\s+' | Where-Object { $_ -ne '' })
    if ($words.Count -eq 0) { return @{ size = $size; lines = @() } }
    while ($true) {
        $font = New-Object System.Drawing.Font($fontName, $size,
                    [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
        $lines = New-Object System.Collections.ArrayList
        $cur = $words[0]
        for ($i = 1; $i -lt $words.Count; $i++) {
            $try = "$cur $($words[$i])"
            if ((Get-Width $try $font) -le $maxWidth) { $cur = $try }
            else { [void]$lines.Add($cur); $cur = $words[$i] }
        }
        [void]$lines.Add($cur)
        $widest = 0
        foreach ($l in $lines) { $w = Get-Width $l $font; if ($w -gt $widest) { $widest = $w } }
        if (($lines.Count -le $maxLines -and $widest -le $maxWidth) -or $size -le $minSize) {
            $font.Dispose()
            return @{ size = $size; lines = $lines }
        }
        $font.Dispose()
        $size = $size - 2
    }
}

$results = @()
foreach ($job in $jobs) {
    $minSize = if ($job.min_size) { [double]$job.min_size } else { [double]$job.size }
    $maxLines = if ($job.max_lines) { [int]$job.max_lines } else { 1 }
    $maxWidth = if ($job.max_width) { [double]$job.max_width } else { 100000 }
    $fit = Fit-Text $job.text $job.font ([double]$job.size) $maxWidth $maxLines $minSize
    $font = New-Object System.Drawing.Font($job.font, $fit.size,
                [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
    $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml($job.color))
    $out = @()
    $n = 0
    foreach ($line in $fit.lines) {
        $sz = $mg.MeasureString($line, $font, [int]::MaxValue, $fmt)
        # Pad generously: Tamil sets marks above and below the base line and a
        # tight box clips them, which is the kind of damage that only shows up
        # at full size after everything is rendered.
        $pad = [int][Math]::Ceiling($fit.size * 0.45)
        $w = [int][Math]::Ceiling($sz.Width) + 2 * $pad
        $h = [int][Math]::Ceiling($sz.Height) + 2 * $pad
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.Clear([System.Drawing.Color]::Transparent)
        $g.TextRenderingHint = 'AntiAlias'
        $g.DrawString($line, $font, $brush, $pad, $pad, $fmt)
        $g.Dispose()
        $file = "$($job.out_prefix)_$n.png"
        $bmp.Save($file, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        $out += @{ file = $file; w = $w; h = $h }
        $n++
    }
    $font.Dispose(); $brush.Dispose()
    $results += @{ id = $job.id; size = $fit.size; lines = $out }
}
$mg.Dispose(); $measure.Dispose()
$json = ConvertTo-Json @($results) -Depth 6
[IO.File]::WriteAllText($OutFile, $json, (New-Object Text.UTF8Encoding $false))
