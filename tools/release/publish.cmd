@echo off
rem Publish a release from the maintainer's PC (see docs/release-notes/README.md).
rem Usage: tools\release\publish.cmd [X.Y.Z]   (default: __version__ from taskbar_battery_widget\__init__.py)
rem Steps: update main, push tag vX.Y.Z, wait for the Release workflow, sign and publish the draft,
rem then delete remote branches already merged into main.
rem Needs: git, GitHub CLI (gh >= 2.49, logged in) and .venv with the signing key set up.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0..\.." || goto :fail

where gh >nul 2>&1 || (echo [x] GitHub CLI ^(gh^) not found. & goto :fail)
if not exist ".venv\Scripts\python.exe" (echo [x] .venv not found in %CD%. & goto :fail)

echo == Updating main
git checkout main || goto :fail
git pull --ff-only origin main || goto :fail

set "VER=%~1"
if "%VER%"=="" for /f tokens^=2^ delims^=^" %%v in ('findstr /b "__version__" taskbar_battery_widget\__init__.py') do set "VER=%%v"
if "%VER%"=="" (echo [x] Could not read __version__. & goto :fail)
set "TAG=v%VER%"
findstr /b /c:"__version__ = \"%VER%\"" taskbar_battery_widget\__init__.py >nul || (echo [x] %TAG% does not match __version__ in main. & goto :fail)
if not exist "docs\release-notes\%TAG%.md" echo [!] docs\release-notes\%TAG%.md is missing: GitHub will generate the notes.

echo.
echo Release %TAG% from main at:
git log -1 --oneline
choice /m "Continue"
if errorlevel 2 goto :end

git ls-remote --exit-code --tags origin "refs/tags/%TAG%" >nul 2>&1
if errorlevel 1 (
    echo == Creating and pushing tag %TAG%
    git tag "%TAG%" 2>nul
    git push origin "%TAG%" || goto :fail
) else (
    echo == Tag %TAG% already on GitHub, skipping
)

echo == Waiting for the Release workflow to start
set "RUN="
for /l %%i in (1,1,30) do if not defined RUN (
    for /f "delims=" %%r in ('gh run list --workflow release.yml --branch "%TAG%" --limit 1 --json databaseId --jq ".[0].databaseId" 2^>nul') do if not "%%r"=="null" set "RUN=%%r"
    if not defined RUN timeout /t 10 /nobreak >nul
)
if not defined RUN (echo [x] No Release workflow run found for %TAG%. & goto :fail)
gh run watch %RUN% --exit-status || (echo [x] The Release workflow failed: gh run view %RUN% --log-failed & goto :fail)

echo == Signing and publishing %TAG%
.venv\Scripts\python tools\signing\sign_release.py %TAG% --publish || goto :fail

echo == Deleting remote branches already merged into main
git fetch --prune origin
for /f "tokens=*" %%b in ('git branch -r --merged origin/main') do (
    set "B=%%b"
    if /i not "!B:~0,7!"=="origin/" set "B="
    if defined B if /i not "!B!"=="origin/main" if "!B:->=!"=="!B!" (
        echo    deleting !B:~7!
        git push origin --delete "!B:~7!"
    )
)

echo.
echo Done: https://github.com/Gabrielfch/taskbar-battery-widget/releases/tag/%TAG%
goto :end

:fail
echo.
echo Release stopped. Nothing after the failed step was run.
endlocal & exit /b 1
:end
endlocal & exit /b 0
