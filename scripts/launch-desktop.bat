@echo off
cd /d "%~dp0.."
python photo_renamer_gui.py %*
if errorlevel 1 pause
