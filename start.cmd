@echo off
cd /d "%~dp0"
echo:
echo   Land or Water panel: http://127.0.0.1:8501/
echo   Keep this window open while the panel is running. Close it to stop.
echo:
.venv\Scripts\python.exe -m land_water serve
