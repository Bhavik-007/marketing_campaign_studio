
@echo off
setlocal EnableExtensions
title Marketing Campaign Studio - GitHub Push

REM ==================================================
REM CONFIGURATION - UPDATE THESE VALUES WHEN NEEDED
REM ==================================================
set "REPO_URL=https://github.com/Bhavik-007/marketing_campaign_studio.git"
set "BRANCH=main"
set "PROJECT_DIR=%~dp0"
REM ==================================================

cd /d "%PROJECT_DIR%" || goto :ERROR

echo.
echo ==========================================
echo     MARKETING CAMPAIGN STUDIO GITHUB PUSH
echo ==========================================
echo Repository: %REPO_URL%
echo Branch:     %BRANCH%
echo.

REM Find Git even if PATH is broken in VS Code
set "GIT_EXE="
where.exe git.exe >nul 2>&1
if not errorlevel 1 set "GIT_EXE=git"

if not defined GIT_EXE if exist "C:\Program Files\Git\cmd\git.exe" set "GIT_EXE=C:\Program Files\Git\cmd\git.exe"
if not defined GIT_EXE if exist "%LOCALAPPDATA%\Programs\Git\cmd\git.exe" set "GIT_EXE=%LOCALAPPDATA%\Programs\Git\cmd\git.exe"

if not defined GIT_EXE (
    echo ERROR: Git was not found.
    goto :ERROR
)

"%GIT_EXE%" --version
if errorlevel 1 goto :ERROR

REM Create/update .gitignore; preserve existing rules
if not exist .gitignore type nul > .gitignore

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p='.gitignore'; $rules=@('.env','.env.*','!.env.example','.venv/','venv/','__pycache__/','*.py[cod]','*.log','.pytest_cache/'); $old=Get-Content $p; foreach($r in $rules){if($old -notcontains $r){Add-Content $p $r; $old += $r}}"

if errorlevel 1 goto :ERROR

REM Initialize Git only when necessary
if not exist ".git" (
    "%GIT_EXE%" init
    if errorlevel 1 goto :ERROR
)

REM Stop if an environment file is already tracked
REM so secrets are not silently pushed.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$g=$env:GIT_EXE; $f=& $g ls-files; if($f | Where-Object { $_ -match '(^|/)\.env($|\.)' -and $_ -notmatch '(^|/)\.env\.example$' }) { exit 1 } else { exit 0 }"

if errorlevel 1 (
    echo.
    echo SECURITY STOP: An .env file is already tracked by Git.
    echo Review the repository history before pushing.
    goto :ERROR
)

REM Configure origin
"%GIT_EXE%" remote get-url origin >nul 2>&1

if errorlevel 1 (
    "%GIT_EXE%" remote add origin "%REPO_URL%"
) else (
    "%GIT_EXE%" remote set-url origin "%REPO_URL%"
)

if errorlevel 1 goto :ERROR

REM Select the target branch
"%GIT_EXE%" checkout -B "%BRANCH%"
if errorlevel 1 goto :ERROR

REM Stage project changes. .gitignore excludes environment
REM files, virtual environments and log files.
"%GIT_EXE%" add -A
if errorlevel 1 goto :ERROR

REM Verify staged files do not include secrets or venv files
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$g=$env:GIT_EXE; $f=& $g diff --cached --name-only; if($f | Where-Object { $_ -match '(^|/)\.env($|\.)' -and $_ -notmatch '(^|/)\.env\.example$' -or $_ -match '(^|/)\.venv/' -or $_ -match '(^|/)venv/' }) { exit 1 } else { exit 0 }"

if errorlevel 1 (
    echo.
    echo SECURITY STOP: Review staged environment files.
    goto :ERROR
)

echo.
echo Changes selected for commit:
"%GIT_EXE%" diff --cached --stat
echo.

set "CONFIRM="
set /p "CONFIRM=Commit and push these changes? Type Y to continue: "

if /i not "%CONFIRM%"=="Y" goto :CANCEL

REM Commit only if there are staged changes
"%GIT_EXE%" diff --cached --quiet

if errorlevel 1 (
    "%GIT_EXE%" commit -m "Update Marketing Campaign Studio"
    if errorlevel 1 goto :ERROR
) else (
    echo No new changes to commit.
)

REM Fetch GitHub branch information
"%GIT_EXE%" fetch origin
if errorlevel 1 goto :ERROR

REM If remote branch exists, prevent overwriting its history
"%GIT_EXE%" show-ref --verify --quiet "refs/remotes/origin/%BRANCH%"

if not errorlevel 1 (
    "%GIT_EXE%" merge-base --is-ancestor "origin/%BRANCH%" HEAD

    if errorlevel 1 (
        echo.
        echo SAFETY STOP: Remote history needs to be reconciled.
        echo No force-push was performed.
        goto :ERROR
    )
)

REM Push without force
"%GIT_EXE%" push -u origin "%BRANCH%"
if errorlevel 1 goto :ERROR

echo.
echo SUCCESS: GitHub push completed.
echo Repository: %REPO_URL%
echo Branch: %BRANCH%
"%GIT_EXE%" status --short --branch
goto :END

:CANCEL
echo.
echo Cancelled. No commit or push was performed.
goto :END

:ERROR
echo.
echo OPERATION FAILED OR STOPPED.
echo Read the error message above before retrying.
set "RESULT=1"
goto :FINISH

:END
set "RESULT=0"

:FINISH
echo.
pause
exit /b %RESULT%
