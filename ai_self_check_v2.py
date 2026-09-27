"""Offline architecture and syntax self-check.

This check is intentionally broker-free. It verifies that the shared trading
engine parses cleanly and that AI remains advisory/support-only: the strategy
is the sole entry authority and AI position-management code is not invoked.
"""
from pathlib import Path
import ast
import re
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
    parsed = {}

    for name in REQUIRED:
        path = ROOT / name
        if not path.is_file():
            failures.append(f"MISSING: {name}")
            continue
        try:
            source = path.read_text(encoding="utf-8")
            parsed[name] = ast.parse(source, filename=name)
        except (SyntaxError, UnicodeError) as exc:
            failures.append(f"INVALID: {name}: {exc}")
        else:
            print(f"OK: {name}")

    bot_path = ROOT / "bot.py"
    if bot_path.is_file():
        source = bot_path.read_text(encoding="utf-8")
        try:
            tree = parsed.get("bot.py") or ast.parse(source, filename="bot.py")

            # AI must never be the executable entry authority.
            if "signal = legacy_signal" not in source:
                failures.append("ARCHITECTURE: strategy signal is not explicitly assigned as the entry signal.")
            if "no_ai_entry_gate=True" not in source:
                failures.append("ARCHITECTURE: AI support-only entry marker is missing.")

            # ai_manage_positions may exist as legacy/advisory code, but it must
            # not be called. A call would allow AI to control exits.
            calls = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "ai_manage_positions":
                        calls.append(node.lineno)
            if calls:
                failures.append(
                    "ARCHITECTURE: ai_manage_positions is invoked at line(s) "
                    + ", ".join(map(str, calls))
                    + "; AI must remain support-only."
                )

            # The active strategy gate must be present in quant_signal_score.
            if "if buy_score >= dynamic_floor" not in source or "if sell_score >= dynamic_floor" not in source:
                failures.append("STRATEGY: dynamic strategy score gate is missing.")
        except Exception as exc:
            failures.append(f"ARCHITECTURE CHECK ERROR: {exc}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print("AI architecture self-check passed: strategy-only entry authority; AI support-only; syntax valid.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
