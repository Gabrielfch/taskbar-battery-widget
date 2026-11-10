# One-shot build: tests -> icon -> PyInstaller (onedir + onefile) -> Inno Setup -> dist\
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' }

function Run($exe, [string[]]$a) {
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "Failed: $exe $($a -join ' ') (exit $LASTEXITCODE)" }
}

$version = (& $py -c "import taskbar_battery_widget as t; print(t.__version__)").Trim()
Write-Host "Building version $version"

Run $py @('-m', 'pytest', '-q')
Run $py @('packaging\make_icon.py')

$buildDir = Join-Path $root 'packaging\build'
if (Test-Path $buildDir) { Remove-Item $buildDir -Recurse -Force }
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
Copy-Item "packaging\build\installer\TaskbarBatteryWidget-Setup-$version.exe" "dist\" -Force
Copy-Item 'packaging\build\dist-onefile\TaskbarBatteryWidget.exe' "dist\TaskbarBatteryWidget-portable-$version.exe" -Force
Get-ChildItem dist\*.exe | ForEach-Object { '{0}  {1:N1} MB' -f $_.Name, ($_.Length / 1MB) }
