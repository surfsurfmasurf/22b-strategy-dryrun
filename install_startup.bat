@echo off
:: Creates a Windows Task Scheduler entry to run on system startup
schtasks /create /tn "22B-Strategy-Engine" /tr "wscript.exe \"%~dp0start_silent.vbs\"" /sc onstart /ru SYSTEM /f
echo Task created. Engine will start automatically on next boot.
echo To start now: schtasks /run /tn "22B-Strategy-Engine"
echo To stop: schtasks /end /tn "22B-Strategy-Engine"
echo To remove: schtasks /delete /tn "22B-Strategy-Engine" /f
pause
