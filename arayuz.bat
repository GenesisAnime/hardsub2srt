@echo off
chcp 65001 >nul
setlocal
set "BASE_URL=http://127.0.0.1:8765"

REM Healthy Hardsub2srt instance? Reuse it; never start a duplicate.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $j=Invoke-RestMethod -UseBasicParsing -Uri '%BASE_URL%/api/durum' -TimeoutSec 3; if ($j.sunucu.port -eq 8765 -and $null -ne $j.kuyruk) { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 goto :reuse

REM A listener that fails the Hardsub health check belongs to another service
REM (or is still starting). Do not launch another copy over it.
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":8765 .*LISTENING"') do set "PORT_PID=%%P"
if defined PORT_PID (
    echo.
    echo [HATA] 8765 portu dolu ^(PID %PORT_PID%^), ancak Hardsub2srt saglik kontrolu basarisiz.
    echo Bu surec durdurulmadi. Portu kullanan uygulamayi kontrol edin.
    pause
    exit /b 1
)

REM Probe each interpreter and Flask together; launch with that exact choice.
set "PY_LAUNCHER="
set "PYTHON_FOUND="
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_FOUND=1"
    py -3 -c "import flask" >nul 2>&1
    if not errorlevel 1 set "PY_LAUNCHER=py -3"
)
if not defined PY_LAUNCHER (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_FOUND=1"
        python -c "import flask" >nul 2>&1
        if not errorlevel 1 set "PY_LAUNCHER=python"
    )
)
if not defined PY_LAUNCHER (
    python3 --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_FOUND=1"
        python3 -c "import flask" >nul 2>&1
        if not errorlevel 1 set "PY_LAUNCHER=python3"
    )
)
if not defined PY_LAUNCHER if defined LOCALAPPDATA if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PYTHON_FOUND=1"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" -c "import flask" >nul 2>&1
    if not errorlevel 1 set "PY_LAUNCHER="%LOCALAPPDATA%\Programs\Python\Python314\python.exe""
)
if not defined PY_LAUNCHER if defined LOCALAPPDATA (
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if not defined PY_LAUNCHER if exist "%%~fD\python.exe" (
            set "PYTHON_FOUND=1"
            "%%~fD\python.exe" -c "import flask" >nul 2>&1
            if not errorlevel 1 set "PY_LAUNCHER="%%~fD\python.exe""
        )
    )
)
if not defined PY_LAUNCHER goto :python_error

start "" "%BASE_URL%"
%PY_LAUNCHER% "%~dp0ui_server.py"
if errorlevel 1 (
    echo.
    echo [HATA] UI sunucusu beklenmeyen bir hatayla kapandi. Yukaridaki Python ciktisini inceleyin.
    pause
    exit /b 1
)
exit /b 0

:reuse
echo [BILGI] Hardsub2srt sunucusu saglikli; ikinci sunucu baslatilmiyor.
start "" "%BASE_URL%"
exit /b 0

:python_error
echo.
if defined PYTHON_FOUND (
    echo [HATA] Python bulundu ancak denenen yorumlayicilarda Flask import edilemedi.
    echo Duzeltme: secilen yorumlayiciya Flask kurun ^(or. py -3 -m pip install flask^).
) else (
    echo [HATA] Python 3 yorumlayicisi bulunamadi ^(py -3, python, python3 denendi^).
    echo Python 3 kurulumunu PATH'e ekleyip bu baslaticiyi yeniden calistirin.
)
pause
exit /b 1
