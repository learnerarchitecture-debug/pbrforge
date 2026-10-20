@echo off
REM ============================================================================
REM  allow-firewall.cmd -- open the Windows firewall so other PCs can connect
REM
REM  Double-click this file. It asks for Administrator itself, so there is no
REM  "right-click, Run as administrator" step to forget.
REM
REM  Why this is needed at all: Windows Firewall blocks inbound connections to a
REM  program that has not been granted an exception. Without this rule, PBRForge
REM  starts perfectly on the server machine, works in its own browser, and is
REM  completely invisible to every other PC on the network. The symptom is "it
REM  works here but not from my laptop", which looks like a PBRForge bug and is
REM  not one.
REM
REM  The rule is scoped to the PRIVATE profile deliberately. It does not open
REM  these ports on a public network -- a cafe, an airport, a hotel -- which is
REM  the difference between a useful convenience and a bad habit.
REM
REM  Usage:
REM      allow-firewall.cmd              uses the default ports 7788 and 7443
REM      allow-firewall.cmd 7790 7444    custom ports
REM      allow-firewall.cmd --remove     deletes the rules again
REM ============================================================================

setlocal EnableDelayedExpansion

REM Defaults first, then override. Doing it in this order matters: the obvious
REM "set HTTP_PORT=%~1" leaves HTTP_PORT literally equal to "--remove" when the
REM flag is used, and the script then tries to open a firewall port called
REM --remove. Defaults-then-override cannot produce that.
set "HTTP_PORT=7788"
set "HTTPS_PORT=7443"
set "REMOVE="
if /I "%~1"=="--remove" set "REMOVE=1"
if /I "%~2"=="--remove" set "REMOVE=1"
if defined REMOVE goto :elevate
if not "%~1"=="" set "HTTP_PORT=%~1"
if not "%~2"=="" set "HTTPS_PORT=%~2"

REM ---------------------------------------------------------------- elevate
:elevate
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Requesting Administrator rights...
    REM --remove has to be forwarded explicitly, or the elevated copy re-adds
    REM the rules it was asked to delete.
    if defined REMOVE (
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
            "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '--remove'"
    ) else (
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
            "Start-Process -Verb RunAs -FilePath '%~f0' -ArgumentList '%HTTP_PORT%','%HTTPS_PORT%'"
    )
    exit /b
)

echo.
echo   PBRForge - firewall rules
echo.

if defined REMOVE (
    call :del "PBRForge TCP %HTTP_PORT%"
    call :del "PBRForge TCP %HTTPS_PORT%"
    call :del "PBRForge UDP 7789"
    echo.
    echo   Rules removed. Other PCs can no longer connect.
    echo.
    goto :done
)

call :add "PBRForge TCP %HTTP_PORT%" TCP %HTTP_PORT%
call :add "PBRForge TCP %HTTPS_PORT%" TCP %HTTPS_PORT%

REM UDP 7789 is the LAN discovery beacon: it lets a client find the server by
REM broadcast instead of being told the IP. Strictly optional -- everything works
REM without it -- but it is what makes `pbrforge discover` on the other PC work.
call :add "PBRForge UDP 7789" UDP 7789

echo.
echo   Done. Other PCs on this network can now reach this machine at:
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=* delims= " %%b in ("%%a") do echo       http://%%b:%HTTP_PORT%
)
echo.
echo   The pairing code is printed by 'pbrforge serve' on this machine.
echo.
goto :done

:add
netsh advfirewall firewall show rule name="%~1" >nul 2>&1
if not errorlevel 1 (
    echo   = already present: %~1
    exit /b 0
)
netsh advfirewall firewall add rule name="%~1" dir=in action=allow protocol=%~2 localport=%~3 profile=private >nul
if errorlevel 1 ( echo   x FAILED: %~1 ) else ( echo   + added: %~1 )
exit /b 0

:del
netsh advfirewall firewall delete rule name="%~1" >nul 2>&1
if errorlevel 1 ( echo   = not present: %~1 ) else ( echo   - removed: %~1 )
exit /b 0

:done
echo.
pause
endlocal
