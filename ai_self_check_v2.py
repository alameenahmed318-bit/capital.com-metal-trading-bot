"""Offline architecture sanity check for legacy queued GitHub Actions runs.

No broker connection, market request, trade placement, or model inference.
"""
from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parent
REQUIRED = (
    "bot.py",
    "fx_ai_bot.py",
    "metals_energy_ai_bot.py",
    "ai_dataset_job.py",
    "ai_outcomes.py",
    "capital_news.py",
)

def main() -> int:
    failures = []
    for name in REQUIRED:
        path = ROOT / name
        if not path.is_file():
            failures.append(f"MISSING: {name}")
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=name)
        except (SyntaxError, UnicodeError) as exc:
            failures.append(f"INVALID: {name}: {exc}")
        else:
            print(f"OK: {name}")
    if failures:
        for issue in failures:
            print(issue, file=sys.stderr)
        return 1
    print("AI architecture self-check passed (offline syntax and presence).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
