@echo off
rem Runs the frontend dev server in THIS window (foreground) so you see live logs. Ctrl+C to stop.
cd /d "%~dp0frontend"
if not exist node_modules\.bin\vite.cmd call npm install
echo Frontend: http://localhost:5173  (proxies /api to http://localhost:8000)
call npm run dev -- --host 127.0.0.1 --port 5173
