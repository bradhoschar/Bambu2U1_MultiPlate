@echo off
title Bambu to Snapmaker U1 Web UI v2.0
if exist "%~dp0Bambu2U1_Converter.exe" (
    "%~dp0Bambu2U1_Converter.exe"
) else (
    python "%~dp0app.py"
)
pause
