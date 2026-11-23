"""AL-ZOOL V2 market-scoped *candidate* replacements, never active by default.

Historical strategy/combination IDs remain unchanged. A V2 signal carries an
explicit catalog version so future attributions must not be combined with V1.
Candidate promotion requires independent validation and MT5 version tagging.
"""
from collections import Counter

VERSION = "AZ-REPLACEMENTS-20261123-V2"
MARKET_DEFINITIONS = {
    "BTCUSD": {
        # Live BTC losing IDs: 12 (-10.69/16), 3 (-5.02/16), 92 (-4.29/14),
        # 21 (-2.65/20). Small samples: these are research candidates ONLY.
        "strategies": {
            12: ("V2 BTC — Liquidity Sweep Reclaim", "sweep_reclaim", "candle"),
            3: ("V2 BTC — Failed Breakout Reversal", "failed_breakout", "candle"),
            92: ("V2 BTC — VWAP Reclaim Momentum", "vwap_reclaim", "candle"),
            21: ("V2 BTC — Compressed Range Release", "compression_release", "close_location"),
        },
        "combinations": {
            1: (11, 30, 68, 101, 151),
            516: (20, 51, 68, 121, 181),
            87: (11, 66, 102, 151, 191),
        },
    },
    "XAUUSD": {
        # Live GOLD losing IDs: 21 (-43.38/64), 1 (-19.49/64),
        # 28 (-23.40/15); keep winners untouched and preserve legacy records.
        "strategies": {
            21: ("V2 GOLD — Trend Retest Continuation", "trend_retest", "candle"),
            1: ("V2 GOLD — Liquidity Sweep Reclaim", "sweep_reclaim", "candle"),
            28: ("V2 GOLD — Failed Breakout Reversal", "failed_breakout", "candle"),
        },
        "combinations": {
            5: (11, 31, 51, 71, 151),
            99: (12, 42, 61, 101, 161),
            47: (11, 53, 91, 131, 171),
        },
    },
}

def market_key(symbol):
    value=str(symbol or "").upper()
    if value.startswith("BTCUSD"):return "BTCUSD"
    if value.startswith("XAUUSD"):return "XAUUSD"
    return None

def build_candidates(base_strategies, base_combinations, symbol):
    """Pure, deterministic shadow catalog. Do not mutate active baseline."""
    market=market_key(symbol)
    if market not in MARKET_DEFINITIONS:
        return None
    selection=MARKET_DEFINITIONS[market]
    strategies=[]
    for original in base_strategies:
        row=dict(original)
        replacement=selection["strategies"].get(row["id"])
        if replacement:
            row.update(name=replacement[0],base_kind=replacement[1],
                       gate_kind=replacement[2],version=VERSION,
                       legacy_name=original["name"])
        strategies.append(row)
    combinations=[]
    for original in base_combinations:
        row=dict(original)
        members=selection["combinations"].get(row["id"])
        if members:
            row["members"]=tuple(sorted(members))
            row["name"]="V2 "+market+" Replacement "+str(row["id"]).zfill(4)
            row["version"]=VERSION
            row["legacy_members"]=tuple(original["members"])
        combinations.append(row)
    ids={s["id"] for s in strategies}
    member_groups=[tuple(c["members"]) for c in combinations]
    assert len(strategies)==200 and len(ids)==200
    assert len(combinations)==1000 and len(set(member_groups))==1000
    assert all(len(m)==5 and len(set(m))==5 and set(m)<=ids for m in member_groups)
    # Preserve 20-family diversity in new 5-member councils.
    by_id={s["id"]:s for s in strategies}
    for combo in combinations:
        if combo.get("version")==VERSION:
            assert len({by_id[sid]["family"] for sid in combo["members"]})==5
    frequencies=Counter(i for combo in combinations for i in combo["members"])
    independence={
        combo["id"]:sum(1/max(1,frequencies[mid]) for mid in combo["members"])/5
        for combo in combinations
    }
    names={
        combo["id"]:" + ".join(by_id[mid]["name"] for mid in combo["members"])
        for combo in combinations
    }
    return {"version":VERSION,"market":market,"strategies":tuple(strategies),
            "combinations":tuple(combinations),"independence":independence,
            "combo_names":names,"retired_strategy_ids":tuple(selection["strategies"]),
            "retired_combo_ids":tuple(selection["combinations"]),
            "production_approved":False}
