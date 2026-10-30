@echo off
chcp 65001 > nul
title Antigravity PC -> Ubuntu Canli Senkronizasyon
echo ========================================================
echo   Antigravity PC -> Ubuntu Konusma Senkronizasyonu
echo ========================================================
python "%~dp0sync_pc_to_ubuntu.py" --loop
pause
