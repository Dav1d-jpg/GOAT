@echo off
cd /d "C:\Users\david\Desktop\GOAT"
start "GOAT Scheduler" venv\Scripts\python.exe scheduler.py
timeout /t 3 /nobreak
start "GOAT Dashboard" venv\Scripts\streamlit.exe run dashboard.py
