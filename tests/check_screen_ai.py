"""How well the Screener reads requests with the real model in .env: every request in screen_cases.py, timed, with
each misread listed. It spends the AI allowance (about 1,500 tokens for each request the rules don't read in full), so
it isn't part of run_all.py.

    .\\.venv\\Scripts\\python tests\\check_screen_ai.py          # all requests
    .\\.venv\\Scripts\\python tests\\check_screen_ai.py --ai     # only those that need the model
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
os.environ.setdefault("STOCKPILOT_WARM", "0")
from backend import main  # noqa: E402,F401  loads .env and the certificates
from backend import ai  # noqa: E402

import screen_cases as sc  # noqa: E402

if not ai.available():
    sys.exit(f"No AI provider is set up: {ai.config().get('problem') or 'set AI_PROVIDER in .env'}")
cases = sc.AI if "--ai" in sys.argv else sc.RULES + sc.AI
model = ai.describe()
print(f"{len(cases)} requests, {model['model']} via {model['server']}\n")
wrong, took = [], []
for q, expected in cases:
    t = time.time()
    try:
        got = ai.parse_query(q)
    except Exception as e:  # noqa: BLE001
        got = {"error": str(e)[:120]}
    took.append(time.time() - t)
    notes = sc.misread(expected, got, q)
    print(f"{'ok  ' if not notes else 'MISS'} {took[-1]:4.1f}s  {q}")
    for n in notes:
        print(f"           {n}")
    if got.get("ignored"):
        print(f"           not applied: {got['ignored']}")
    if notes:
        wrong.append(q)
print(f"\n{len(cases) - len(wrong)} of {len(cases)} read as expected; {sum(took):.0f}s in all, slowest {max(took):.1f}s")
