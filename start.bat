@echo off
cd /d "%~dp0"

echo Restarting Ollama with CORS enabled...
taskkill /f /im ollama.exe > nul 2>&1
set OLLAMA_ORIGINS=*
start "" /MIN "ollama" serve
timeout /t 3 /nobreak > nul

echo Generating system info...
echo window.sysinfo_data = ` > sysinfo.js
systeminfo >> sysinfo.js
echo ` >> sysinfo.js
echo Starting Ollama UI...
start "" "ollama-chat.html"
