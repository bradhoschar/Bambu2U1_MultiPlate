@echo off
title Bambu to Snapmaker U1 Multi-Plate Converter v2.0
if "%~1"=="" (
    echo [Bambu to Snapmaker U1 Converter v2.0]
    echo.
    echo Drag and drop any Bambu .3mf file onto this icon to convert it!
    echo Or launching the Web UI now...
    if exist "%~dp0Bambu2U1_Converter.exe" (
        start "" "%~dp0Bambu2U1_Converter.exe"
    ) else (
        python "%~dp0app.py"
    )
    exit /b
)

echo Converting "%~nx1" to Snapmaker U1 plates (v2.0 Engine)...
python "%~dp0converter.py" "%~1"
echo.
echo Done! Converted plates saved to: "%~dpn1_U1_Ready"
pause
