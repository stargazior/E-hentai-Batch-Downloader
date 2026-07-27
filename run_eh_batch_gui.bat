@echo off
setlocal
set SCRIPT_DIR=%~dp0
set GUI_SCRIPT=%SCRIPT_DIR%eh_batch_gui.py

if exist "C:\Users\hoshizora\.conda\envs\pytorch\python.exe" (
  "C:\Users\hoshizora\.conda\envs\pytorch\python.exe" "%GUI_SCRIPT%"
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py "%GUI_SCRIPT%"
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%GUI_SCRIPT%"
  exit /b %ERRORLEVEL%
)

echo Python was not found. Install Python or edit this file to point to python.exe.
pause
exit /b 1
