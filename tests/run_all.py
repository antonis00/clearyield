"""Run every check. Each browser check gets its own server and temporary database, so your data isn't touched.

    python tests/run_all.py                 everything
    python tests/run_all.py --offline       skip the checks that need live Yahoo data
    python tests/run_all.py --only NAME     one check, e.g. walk-demo, walk-live, check_live, or a name from browser/checks.js

First run: `npm install` in tests/browser. The browser checks use your installed Chrome.
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BROWSER = ROOT / "tests" / "browser"
OUT = BROWSER / "out"
FOCUSED = ["incomplete", "sortbuttons", "backup", "xss", "alertdigest", "race", "notify", "pagefixes", "ledger"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def with_server(demo, command):
    """Run `command(base_url)` against a fresh server, always on current code and an empty database."""
    port = free_port()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "STOCKPILOT_DB": str(Path(tmp) / "check.db")}
        env.pop("ANTHROPIC_API_KEY", None)
        env["STOCKPILOT_DEMO"] = "1" if demo else "0"
        log = open(Path(tmp) / "server.log", "w")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port), "--log-level", "warning"],
                                  cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            base = f"http://127.0.0.1:{port}/"
            for _ in range(60):
                try:
                    urllib.request.urlopen(base, timeout=1)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("The server didn't start.")
            return command(base)
        finally:
            server.terminate()
            server.wait(timeout=15)
            log.close()


def node(script, *args):
    return subprocess.run([shutil.which("node"), script, *args], cwd=BROWSER).returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    checks = [("offline tests", lambda: subprocess.run([py, "tests/test_smoke.py"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode)]
    checks += [(name, lambda n=name: with_server(True, lambda base: node("checks.js", base, n, str(OUT)))) for name in FOCUSED]
    checks.append(("walk-demo", lambda: with_server(True, lambda base: node("walk.js", base, str(OUT), "demo"))))
    if not a.offline:
        checks.append(("walk-live", lambda: with_server(False, lambda base: node("walk.js", base, str(OUT), "live"))))
        checks.append(("check_live", lambda: subprocess.run([py, "tests/check_live.py"], cwd=ROOT).returncode))
    if a.only:
        checks = [c for c in checks if c[0] == a.only]
        if not checks:
            ap.error(f"no check called {a.only}")

    if not (BROWSER / "node_modules").exists() and any(c[0] not in ("offline tests", "check_live") for c in checks):
        print("Run `npm install` in tests/browser first.")
        return 2

    results = []
    for name, run in checks:
        print(f"\n=== {name}", flush=True)
        results.append((name, run() == 0))
    print("\n=== summary")
    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}")
    return 0 if all(p for _, p in results) else 1


if __name__ == "__main__":
    sys.exit(main())
