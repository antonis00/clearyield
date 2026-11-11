"""Start with Windows: a shortcut in your Startup folder runs the app in the background (backend/background.py) when
you sign in, so alerts, emails and the daily scoring carry on with the page closed. Nothing needs administrator rights,
and removing the shortcut undoes it."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "ClearYield.lnk"


def folder():
    """Your Startup folder (CLEARYIELD_STARTUP_DIR stands in for it in the tests)."""
    if os.environ.get("CLEARYIELD_STARTUP_DIR"):
        return Path(os.environ["CLEARYIELD_STARTUP_DIR"])
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def supported():
    return os.name == "nt"


def status():
    return {"supported": supported(), "enabled": supported() and (folder() / NAME).exists()}


def _pythonw():
    """The windowless Python beside the one running the app (.venv\\Scripts\\pythonw.exe)."""
    w = Path(sys.executable).with_name("pythonw.exe")
    return w if w.exists() else Path(sys.executable)


def _ps_quote(v):
    return "'" + str(v).replace("'", "''") + "'"


def enable():
    if not supported():
        raise ValueError("Starting with the computer is only set up for Windows.")
    target = folder() / NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(target)}); "
              f"$s.TargetPath = {_ps_quote(_pythonw())}; $s.Arguments = '-m backend.background'; "
              f"$s.WorkingDirectory = {_ps_quote(ROOT)}; $s.WindowStyle = 7; "
              f"$s.Description = 'ClearYield in the background (http://localhost:8001)'; $s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30)
    if r.returncode or not target.exists():
        raise ValueError(f"Couldn't add the shortcut to the Startup folder: {(r.stderr or r.stdout).strip()[:300]}")
    return status()


def disable():
    (folder() / NAME).unlink(missing_ok=True)
    return status()
