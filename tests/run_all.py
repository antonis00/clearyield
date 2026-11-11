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
FOCUSED = ["incomplete", "sortbuttons", "backup", "xss", "alertdigest", "race", "notify", "pagefixes", "ledger", "periods", "backbutton", "pence", "fundrows", "features"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Result:
    """How a check went: passed or not, the steps that failed, and anything else worth showing when it didn't pass."""

    def __init__(self, passed, failures=(), detail=""):
        self.passed, self.failures, self.detail = passed, list(failures), detail


def tail(text, lines=40):
    return "\n".join(text.strip().splitlines()[-lines:])


def stream(cmd, cwd):
    """Run a command, echoing its output as it comes but leaving out the app's own log lines, and keep each step
    that reported FAIL for the summary."""
    # Unbuffered, so the output shows as it comes; UTF-8, since a Windows pipe is otherwise in the ANSI code page
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    failures, kept = [], []
    for line in p.stdout:
        kept.append(line)
        if line.startswith(("INFO:", "WARNING:", "DEBUG:")):
            continue
        print(line, end="", flush=True)
        if line.startswith(("FAIL ", "MISMATCH ")):
            failures.append(line.strip())
    passed = p.wait() == 0
    if not passed and not failures:  # it stopped without naming a step (a crash): its last words say why
        last = [line.strip() for line in kept if line.strip()]
        failures.append(last[-1] if last else f"exited with code {p.returncode} and no output")
    return Result(passed, failures, tail("".join(kept)))


def with_server(demo, command):
    """Run `command(base_url)` against a fresh server, always on current code and an empty database. If the check
    fails, the server's own log comes back with it, since it would otherwise go with the temporary folder."""
    port = free_port()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {**os.environ, "CLEARYIELD_DB": str(Path(tmp) / "check.db")}
        env.pop("ANTHROPIC_API_KEY", None)
        env["AI_PROVIDER"], env["WEB_SEARCH"], env["CLEARYIELD_WARM"] = "none", "off", "0"  # the browser checks expect rule-based briefs, not a wait on a model from .env
        env["CLEARYIELD_DEMO"] = "1" if demo else "0"
        if not demo:  # sector ranks against the first three markets only, so a run doesn't download the whole list
            env["CLEARYIELD_PEER_MARKETS"] = "Europe,Middle East & Africa"
        log_path = Path(tmp) / "server.log"
        log = open(log_path, "w", encoding="utf-8")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(port), "--log-level", "warning"],
                                  cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            base = f"http://127.0.0.1:{port}/"
            for _ in range(60):
                if server.poll() is not None:
                    break
                try:
                    urllib.request.urlopen(base, timeout=1)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                server.kill()
            if server.poll() is not None:
                log.close()
                return Result(False, ["The server didn't start."], "server log:\n" + tail(log_path.read_text(encoding="utf-8", errors="replace")))
            result = command(base)
        finally:
            server.terminate()
            server.wait(timeout=15)
            log.close()
        if not result.passed:
            server_log = tail(log_path.read_text(encoding="utf-8", errors="replace")) or "(empty)"
            result.detail = (result.detail + "\n\n" if result.detail else "") + "server log:\n" + server_log
        return result


def node(script, *args):
    return stream([shutil.which("node"), script, *args], BROWSER)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only")
    a = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")  # a character the console can't show becomes "?", instead of stopping the run

    OUT.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    checks = [("offline tests", lambda: stream([py, "tests/test_smoke.py"], ROOT))]
    checks += [(name, lambda n=name: with_server(True, lambda base: node("checks.js", base, n, str(OUT)))) for name in FOCUSED]
    checks.append(("walk-demo", lambda: with_server(True, lambda base: node("walk.js", base, str(OUT), "demo"))))
    if not a.offline:
        checks.append(("walk-live", lambda: with_server(False, lambda base: node("walk.js", base, str(OUT), "live"))))
        checks.append(("check_live", lambda: stream([py, "tests/check_live.py"], ROOT)))
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
        start = time.perf_counter()
        try:
            r = run()
        except Exception as e:  # a check that couldn't run at all still gets its line in the summary
            r = Result(False, [f"Couldn't run: {e}"])
        took = time.perf_counter() - start
        if not r.passed and r.detail:
            print(f"\n--- {name} failed; the end of its output, for context:\n{r.detail}", flush=True)
        results.append((name, r, took))
    print("\n=== summary")
    for name, r, took in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {name}  ({took:.0f}s)")
        for f in r.failures:
            print(f"        {f}")
    failed = [name for name, r, _ in results if not r.passed]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
