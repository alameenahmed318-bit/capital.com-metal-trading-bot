"""Offline architecture and syntax self-check.

The active architecture intentionally uses one simple market strategy rather
than the retired dynamic score-gate system. This checker validates the current
strategy contract instead of requiring legacy score variables.
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

            if "legacy_signal = generate_signal(" not in source:
                failures.append(
                    "ARCHITECTURE: strategy signal is not explicitly assigned as the entry signal."
                )
            if "no_ai_entry_gate=True" not in source:
                failures.append(
                    "ARCHITECTURE: AI support-only entry marker is missing."
                )

            calls = []
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "ai_manage_positions"
                ):
                    calls.append(node.lineno)
            if calls:
                failures.append(
                    "ARCHITECTURE: ai_manage_positions is invoked at line(s) "
                    + ", ".join(map(str, calls))
                    + "; AI must remain support-only."
                )

            if "def market_strategy_signal(" not in source:
                failures.append("STRATEGY: active market_strategy_signal() is missing.")
            if "strategy_signal = legacy_signal" not in source:
                failures.append("STRATEGY: strategy signal is not the sole entry authority.")

        except Exception as exc:
            failures.append(f"ARCHITECTURE CHECK ERROR: {exc}")

    fx_path = ROOT / "fx_ai_bot.py"
    if fx_path.is_file():
        source = fx_path.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename="fx_ai_bot.py")
            if "base.REVERSE_ENTRY_DIRECTION = False" not in source:
                failures.append("FX: normal execution direction is not enabled.")
        except Exception as exc:
            failures.append(f"FX CHECK ERROR: {exc}")

    metals_path = ROOT / "metals_energy_ai_bot.py"
    if metals_path.is_file():
        source = metals_path.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename="metals_energy_ai_bot.py")
            if "base.MAX_POSITIONS_PER_EPIC = None" not in source:
                failures.append("METALS: artificial per-epic position cap is enabled.")
            if "base.REVERSE_ENTRY_DIRECTION = False" not in source:
                failures.append("METALS: normal execution direction is not enabled.")
            if "def iron_signal(" not in source:
                failures.append("METALS: METAL IMPERIUM IRON strategy is missing.")
        except Exception as exc:
            failures.append(f"METALS CHECK ERROR: {exc}")

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(
        "AI architecture self-check passed: "
        "single strategy entry authority; AI support-only; "
        "FX and metals normal execution direction; syntax valid."
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
