@echo off
cd /d "%~dp0"
echo Generating system info...
echo window.sysinfo_data = ` > sysinfo.js
systeminfo >> sysinfo.js
echo ` >> sysinfo.js
echo Starting Ollama UI...
start "" "ollama-chat.html"
