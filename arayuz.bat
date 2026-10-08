@echo off
chcp 65001 >nul
REM ============================================================
REM  hardsub2srt - web arayuzu baslatici
REM  Tarayiciyi acar (http://127.0.0.1:8765) ve sunucuyu baslatir.
REM  Port zaten kullanimdaysa sunucu "zaten calisiyor" deyip cikar.
REM ============================================================
start "" http://127.0.0.1:8765
py -3 "%~dp0ui_server.py"
if errorlevel 1 (
    echo.
    echo [HATA] Sunucu baslatilamadi. Python "py" komutu kurulu olmali;
    echo Flask icin: py -3 -m pip install flask
    pause
)
