"""Run ClearYield with no window, as Windows starts it:  pythonw -m backend.background

Output goes to data/server.log (the previous one kept as server.log.1 once it passes LOG_MAX), since pythonw has no
console. If the app already answers on the port, this leaves it be and exits, so a second start does nothing."""
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("CLEARYIELD_PORT", "8001"))
LOG_MAX = 5_000_000


def running(port=PORT):
    """Whether something already answers on the port on this computer."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _log_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_MAX:
        path.replace(path.with_name(path.name + ".1"))
    return open(path, "a", encoding="utf-8", buffering=1)


def main():
    log = _log_file(Path(os.environ.get("CLEARYIELD_LOG") or ROOT / "data" / "server.log"))
    sys.stdout = sys.stderr = log  # pythonw has neither
    if running():
        print(f"ClearYield already answers on port {PORT}; not starting another.")
        return
    os.chdir(ROOT)
    import uvicorn
    print(f"ClearYield starting in the background on http://localhost:{PORT}")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=PORT, log_level="info", log_config=None)


if __name__ == "__main__":
    main()
