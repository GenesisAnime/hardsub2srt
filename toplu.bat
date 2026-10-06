@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
REM ============================================================
REM  hardsub2srt - toplu altyazi cikarici (klasor veya coklu dosya)
REM  Kullanim:
REM    - Video KLASORUNU bu .bat uzerine surukleyip birakin:
REM      icindeki tum mp4/mkv/avi dosyalari sirayla islenir.
REM    - Birden fazla video DOSYASI birakmak da desteklenir,
REM      dogrudan sirayla islenir.
REM    - Cift tiklayip klasor yolunu elle de yazabilirsiniz.
REM  Altyazi stili (bant konumu + renk modu) OTOMATIK algilanir,
REM  profil sorulmaz.
REM  Cikti SRT'ler her zaman BU .bat'in bulundugu klasore yazilir.
REM  Her kosuda qa/ klasorune 4 adet kontrol montaji dusar.
REM  Basariyla islenen her video %~dp0kosuldu.list dosyasina yazilir;
REM    ayni video (ad+boyut ayniysa) bir dahaki kosusta ATLANIR.
REM  Listeyi takilmak istemiyorsaniz ilk arguman olarak --yeni verin:
REM    toplu.bat --yeni "D:\videolar"
REM ============================================================

set "PYTHON=py -3"
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo [HATA] Python "py" komutu bulunamadi. Python kurulu olmali ve PATH'te olmali.
    pause
    exit /b 1
)

set /a TOPLAM=0
set /a SAYAC=0
set /a BASARILI=0
set /a HATALI=0
set /a ATLANAN=0
set "YENI="
rem kosuldu.list bu .bat'in yandasinda tutulur (ad|boyut satirlari)
set "LISTE=%~dp0kosuldu.list"

if not "%~1"=="" goto :ARGV
echo Video klasorunu - veya video dosyalarini - bu .bat uzerine
echo surukleyip birakabilirsiniz, ya da klasor yolunu buraya
echo yazip Enter'a basin:
set /p "INPUT=Klasor yolu: "
if not defined INPUT (
    echo [HATA] Klasor yolu bos birakildi.
    pause
    exit /b 1
)
rem tirnaklari temizle (konsola elle yapistirilan yollar icin)
set INPUT=!INPUT:"=!
if not defined INPUT (
    echo [HATA] Klasor yolu bos birakildi.
    pause
    exit /b 1
)
call :SAY "%INPUT%"
goto :HAZIRLA

:ARGV
call :SAY %*

:HAZIRLA
if !TOPLAM!==0 (
    echo [UYARI] Islenecek mp4/mkv/avi video bulunamadi.
    pause
    exit /b 1
)

echo.
echo NOT: Ilk calistirmada EasyOCR modeli indirilir, internet gerekir.
echo      Pencereyi KAPATMAYIN, tum videolar bitene kadar bekleyin.

if not "%~1"=="" goto :ARGV2
call :KOS "%INPUT%"
goto :OZET

:ARGV2
call :KOS %*

:OZET
echo.
echo ================================================================
if defined YENI echo  Mod: --yeni (kosuldu.list takilmadi, hepsi islendi)
echo  Toplu cikarma bitti: !BASARILI! basarili, !HATALI! hatali, !ATLANAN! atlandi.
echo  SRT ciktilari     : %~dp0
echo  QA kontrol montaji: %~dp0qa
echo  Kosuldu listesi   : !LISTE!
echo ================================================================
pause
exit /b 0

rem ---- yardimci: oge sayimi (dosya=1, klasor=icindeki videolar) ----
:SAY
if "%~1"=="" goto :eof
if /i "%~1"=="--yeni" (
    set YENI=1
    shift
    goto :SAY
)
set "ARG=%~1"
if exist "!ARG!\" (
    pushd "!ARG!" 2>nul || (
        shift
        goto :SAY
    )
    for %%V in (*.mp4 *.mkv *.avi) do set /a TOPLAM+=1
    popd
) else (
    set /a TOPLAM+=1
)
shift
goto :SAY

rem ---- yardimci: oge isleme listesi ----
:KOS
if "%~1"=="" goto :eof
if /i "%~1"=="--yeni" (
    set YENI=1
    shift
    goto :KOS
)
set "ARG=%~1"
if exist "!ARG!\" (
    pushd "!ARG!" 2>nul || (
        echo [HATA] Klasor acilamadi, atlandi: !ARG!
        set /a HATALI+=1
        shift
        goto :KOS
    )
    for %%V in (*.mp4 *.mkv *.avi) do (
        set /a SAYAC+=1
        call :ISLE "%%~fV"
    )
    popd
) else (
    set /a SAYAC+=1
    call :ISLE "!ARG!"
)
shift
goto :KOS

rem ---- tek video isleyici ----
:ISLE
set "VIDPATH=%~1"
set "VIDNAME=%~nx1"
set "VIDOUT=%~dp0%~n1.srt"
if not exist "!VIDPATH!" (
    echo.
    echo [HATA] Dosya bulunamadi, atlandi: !VIDPATH!
    set /a HATALI+=1
    goto :eof
)
set "VIDSIZE=%~z1"
set "ENTRY=!VIDNAME!|!VIDSIZE!"
if not defined YENI if exist "!LISTE!" (
    findstr /X /C:"!ENTRY!" "!LISTE!" >nul 2>&1 && (
        echo [Atlandi: !SAYAC!/!TOPLAM!] !VIDNAME! (kosuldu.list'te kayitli)
        set /a ATLANAN+=1
        goto :eof
    )
)
echo.
echo ================================================================
echo  [Ilerleme: !SAYAC!/!TOPLAM!] Isleniyor: !VIDNAME!
echo ================================================================
echo.
%PYTHON% "%~dp0hardsub2srt.py" "!VIDPATH!" -o "!VIDOUT!" --qa 4 --qa-dir "%~dp0qa"
if errorlevel 1 (
    echo.
    echo [HATA] Cikarma basarisiz, siradaki videoya geciliyor: !VIDNAME!
    set /a HATALI+=1
) else (
    echo.
    echo [Bitti: !SAYAC!/!TOPLAM!] !VIDNAME! --^> SRT: !VIDOUT!
    >>"!LISTE!" echo !ENTRY!
    set /a BASARILI+=1
)
goto :eof
