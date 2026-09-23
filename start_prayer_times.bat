@echo off
REM Prayer Times host launcher (HermesPc) — starts the self-healing watchdog.
REM The watchdog keeps `python server.py 8787` and the Cloudflare tunnel alive,
REM and writes a daily Tier-2 backup to backups\ (kept 30 snapshots).
REM Scheduled-task belt: "PrayerTimesHost Watchdog" runs this watchdog every minute
REM (registered via schtasks), so after a reboot the services come back within ~1 min
REM of your first logon — no manual step.
cd /d "%~dp0"
echo Starting Prayer Times watchdog ...
start "" /min "C:\Users\LENOVO\AppData\Local\Programs\KiCad\10.0\bin\pythonw.exe" "watchdog.py"
timeout /t 20 /nobreak >nul
echo.
echo Backend health:
curl -s --max-time 5 http://127.0.0.1:8787/api/health
echo.
echo Public URL (current quick-tunnel shot; rotates if the tunnel ever restarts):
findstr trycloudflare.com tunnel.log
echo.
echo Watchdog log: watchdog.log   |   Tier-2 backups: backups\
pause