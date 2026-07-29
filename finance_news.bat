@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ==========================================================================
rem  finance_news.bat - launcher for the finance news scraper (Windows)
rem
rem  Double-click for a menu, OR run from a terminal with any command, e.g.:
rem      finance_news.bat scrape
rem      finance_news.bat top --limit 40
rem      finance_news.bat report --open
rem  (anything after the .bat name is passed straight to finance_news.py)
rem ==========================================================================

rem --- locate Python (prefer the py launcher, fall back to python) -----------
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3 was not found on your PATH. Install it from python.org and retry.
    pause
    exit /b 1
)

set "SCRIPT=%~dp0finance_news.py"

rem --- pass-through mode: if any arguments were given, forward them ----------
if not "%~1"=="" (
    %PY% "%SCRIPT%" %*
    exit /b %errorlevel%
)

rem --- interactive menu (double-click) --------------------------------------
:menu
cls
echo ============================================================
echo    FINANCE NEWS  -  macro headlines from the last 24 hours
echo ============================================================
echo.
echo    1.  Update news  and  open the printable report   (daily driver)
echo    2.  Update news  and  show headlines here
echo    3.  Open the latest report (no update)
echo    4.  Check which sources are live
echo    5.  Show database stats
echo    6.  Quit
echo.
set /p "choice=Choose 1-6: "

if "%choice%"=="1" (
    %PY% "%SCRIPT%" scrape
    %PY% "%SCRIPT%" report --open
    echo.
    pause
    goto menu
)
if "%choice%"=="2" (
    %PY% "%SCRIPT%" scrape
    %PY% "%SCRIPT%" top --limit 40
    echo.
    pause
    goto menu
)
if "%choice%"=="3" (
    set "latest="
    for /f "delims=" %%F in ('dir /b /a-d /o-d "news_report_*.html" 2^>nul') do (
        if not defined latest set "latest=%%F"
    )
    if defined latest (
        start "" "%~dp0!latest!"
    ) else (
        echo No report found yet. Run option 1 first.
        pause
    )
    goto menu
)
if "%choice%"=="4" (
    %PY% "%SCRIPT%" sources --check
    echo.
    pause
    goto menu
)
if "%choice%"=="5" (
    %PY% "%SCRIPT%" stats
    echo.
    pause
    goto menu
)
if "%choice%"=="6" exit /b 0

echo Invalid choice - please enter a number from 1 to 6.
timeout /t 1 >nul
goto menu
