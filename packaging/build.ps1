# One-shot build: tests -> icon -> PyInstaller (onedir + onefile) -> Inno Setup -> dist\ + SHA256SUMS.txt
# Usage: powershell -File packaging\build.ps1 [-SkipTests]
# The version comes ONLY from taskbar_battery_widget.__version__ (version_info.txt is rendered from a template,
# the installer receives /DAppVersion).
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Python: project venv if present, otherwise whatever "python" is on PATH (CI).
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

# Inno Setup compiler: $env:ISCC override, standard install locations, then PATH.
function Find-Iscc {
    $cands = @()
    if ($env:ISCC) { $cands += $env:ISCC }
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if ($base) { $cands += (Join-Path $base 'Inno Setup 6\ISCC.exe') }
    }
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'ISCC.exe not found. Install Inno Setup 6 or set the ISCC environment variable.'
}
$iscc = Find-Iscc

function Run($exe, [string[]]$a) {
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "Failed: $exe $($a -join ' ') (exit $LASTEXITCODE)" }
}

$version = (& $py -c "import taskbar_battery_widget as t; print(t.__version__)").Trim()
if ($version -notmatch '^(\d+)\.(\d+)\.(\d+)$') { throw "Unexpected version '$version' (expected MAJOR.MINOR.PATCH)" }
$maj, $min, $pat = $Matches[1], $Matches[2], $Matches[3]
Write-Host "Building version $version (ISCC: $iscc)"

# Render version_info.txt from the template.
$tpl = Get-Content -LiteralPath (Join-Path $root 'packaging\version_info.template.txt') -Raw
$tpl = $tpl.Replace('@MAJOR@', $maj).Replace('@MINOR@', $min).Replace('@PATCH@', $pat).Replace('@VERSION@', $version)
[System.IO.File]::WriteAllText((Join-Path $root 'packaging\version_info.txt'), $tpl, (New-Object System.Text.UTF8Encoding($false)))

if (-not $SkipTests) { Run $py @('-m', 'pytest', '-q') } else { Write-Host 'Skipping tests (-SkipTests)' }
Run $py @('packaging\make_icon.py')

$buildDir = Join-Path $root 'packaging\build'
if (Test-Path -LiteralPath $buildDir) { Remove-Item -LiteralPath $buildDir -Recurse -Force }
$spec = 'packaging\taskbar_battery_widget.spec'
$pi = @('-m', 'PyInstaller', '--noconfirm', '--clean')

# onedir (installer payload)
Remove-Item Env:TBW_ONEFILE -ErrorAction SilentlyContinue
Run $py ($pi + @('--distpath', 'packaging\build\dist', '--workpath', 'packaging\build\work', $spec))

# onefile (portable)
$env:TBW_ONEFILE = '1'
Run $py ($pi + @('--distpath', 'packaging\build\dist-onefile', '--workpath', 'packaging\build\work-onefile', $spec))
Remove-Item Env:TBW_ONEFILE

Run $iscc @("/DAppVersion=$version", 'packaging\installer.iss')

New-Item -ItemType Directory -Force dist | Out-Null
$setupName = "TaskbarBatteryWidget-Setup-$version.exe"
$portableName = "TaskbarBatteryWidget-portable-$version.exe"
Copy-Item -LiteralPath "packaging\build\installer\$setupName" 'dist\' -Force
Copy-Item -LiteralPath 'packaging\build\dist-onefile\TaskbarBatteryWidget.exe' "dist\$portableName" -Force

# SHA256SUMS.txt: "<hash>  <name>" (sha256sum format, LF, no BOM), only this version's artifacts.
$lines = foreach ($n in @($setupName, $portableName)) {
    '{0}  {1}' -f (Get-FileHash -LiteralPath (Join-Path $root "dist\$n") -Algorithm SHA256).Hash.ToLower(), $n
}
[System.IO.File]::WriteAllText((Join-Path $root 'dist\SHA256SUMS.txt'), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))

Get-ChildItem -LiteralPath dist -Filter "*$version.exe" | ForEach-Object { '{0}  {1:N1} MB' -f $_.Name, ($_.Length / 1MB) }
Write-Host '--- dist\SHA256SUMS.txt ---'
Get-Content -LiteralPath dist\SHA256SUMS.txt
