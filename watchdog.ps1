# GOAT Watchdog – startet Scheduler und Dashboard neu, falls sie nicht laufen.
# Wird vom Windows Task Scheduler alle 5 Minuten ausgefuehrt ("GOAT Watchdog").
#
# Bot absichtlich stoppen:  STOP_BOT.txt im Projektordner anlegen
#                           (dann Prozesse beenden – Watchdog startet nichts neu)
# Bot wieder freigeben:     STOP_BOT.txt loeschen
#
# Logs: scheduler.log / dashboard.log (werden bei jedem Neustart ueberschrieben)

$proj = "C:\Users\david\Desktop\GOAT"

if (Test-Path "$proj\STOP_BOT.txt") {
    exit 0
}

# Mutex verhindert dass zwei Watchdog-Laeufe gleichzeitig Prozesse starten
# (z.B. manueller Aufruf parallel zum Task-Scheduler-Lauf)
$mutex = New-Object System.Threading.Mutex($false, "Global\GOATWatchdog")
if (-not $mutex.WaitOne(0)) {
    exit 0
}

$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'streamlit.exe'"
$sched = $procs | Where-Object { $_.CommandLine -match 'scheduler\.py' }
$dash  = $procs | Where-Object { $_.CommandLine -match 'dashboard\.py' }

if (-not $sched) {
    Start-Process -FilePath "$proj\venv\Scripts\python.exe" `
        -ArgumentList "scheduler.py" `
        -WorkingDirectory $proj -WindowStyle Hidden `
        -RedirectStandardOutput "$proj\scheduler.log" `
        -RedirectStandardError  "$proj\scheduler_error.log"
}

if (-not $dash) {
    Start-Process -FilePath "$proj\venv\Scripts\streamlit.exe" `
        -ArgumentList "run", "dashboard.py", "--server.headless", "true" `
        -WorkingDirectory $proj -WindowStyle Hidden `
        -RedirectStandardOutput "$proj\dashboard.log" `
        -RedirectStandardError  "$proj\dashboard_error.log"
}
