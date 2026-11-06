@echo off
title Bambu to Snapmaker U1 Multi-Plate Converter
if "%~1"=="" (
    echo [Bambu to Snapmaker U1 Converter]
    echo.
    echo Drag and drop any Bambu .3mf file onto this icon to convert it!
    echo Or launching the Web UI now...
    python "%~dp0app.py"
    pause
    exit /b
)

echo Converting "%~nx1" to Snapmaker U1 plates...
python "%~dp0converter.py" "%~1"
echo.
echo Done! Converted plates saved to: "%~dpn1_U1_Ready"
pause
