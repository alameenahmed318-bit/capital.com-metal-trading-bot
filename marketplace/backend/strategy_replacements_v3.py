"""Additional market-specific V3 *research-only* candidate replacements.

Independent version namespace; V1/V2 trade histories remain attributable to
their original definitions. Never activated through the default live path.
"""
from collections import Counter
from strategy_replacements import build_candidates, MARKET_DEFINITIONS, market_key

VERSION_V3="AZ-REPLACEMENTS-20261123-V3"
EXTRA_CANDIDATES={
 "BTCUSD":{
  # BTC attributed realized losses, small samples. Evidence is preliminary.
  "strategies":{
   18:("V3 BTC — Wick Reversal Reclaim","wick_reversal","candle"),
   28:("V3 BTC — EMA Continuation Break","ema_continuation","close_location"),
  },
  "combinations":{
   76:(11,30,68,151,191),
   978:(20,30,68,141,181),
  }},
 "XAUUSD":{
  "strategies":{
   27:("V3 GOLD — Wick Reversal Reclaim","wick_reversal","candle"),
   6:("V3 GOLD — Midrange Reversion","median_reversion","candle"),
   2:("V3 GOLD — EMA Continuation Break","ema_continuation","close_location"),
  },
  "combinations":{
   4:(11,31,61,101,151),
   215:(12,51,71,111,191),
  }}
}

def build_v3_candidates(original_strategies,original_combinations,symbol):
    market=market_key(symbol)
    if market not in EXTRA_CANDIDATES:return None
    base=build_candidates(original_strategies,original_combinations,symbol)
    extras=EXTRA_CANDIDATES[market]
    strategies=[]
    original_by_id={x["id"]:x for x in original_strategies}
    for item in base["strategies"]:
        row=dict(item)
        override=extras["strategies"].get(row["id"])
        if override:
            row.update(name=override[0],base_kind=override[1],
                       gate_kind=override[2],legacy_name=original_by_id[row["id"]]["name"])
        if "version" in row or override:row["version"]=VERSION_V3
        strategies.append(row)
    combinations=[]
    original_combo_by_id={x["id"]:x for x in original_combinations}
    for item in base["combinations"]:
        row=dict(item)
        members=extras["combinations"].get(row["id"])
        if members:
            row.update(members=tuple(sorted(members)),
                       name="V3 "+market+" Extra "+str(row["id"]).zfill(4),
                       legacy_members=tuple(original_combo_by_id[row["id"]]["members"]))
        if "version" in row or members:row["version"]=VERSION_V3
        combinations.append(row)
    ids={x["id"] for x in strategies}
    member_groups=[tuple(c["members"]) for c in combinations]
    assert len(strategies)==len(ids)==200
    assert len(combinations)==len(set(member_groups))==1000
    by_id={s["id"]:s for s in strategies}
    for members in member_groups:
        assert len(members)==len(set(members))==5
        assert set(members)<=ids
    for item in combinations:
        if item.get("version")==VERSION_V3:
            assert len({by_id[k]["family"] for k in item["members"]})==5
    freqs=Counter(k for c in combinations for k in c["members"])
    independence={c["id"]:sum(1/max(1,freqs[k]) for k in c["members"])/5 for c in combinations}
    names={c["id"]:" + ".join(by_id[k]["name"] for k in c["members"]) for c in combinations}
    return {"version":VERSION_V3,"market":market,
            "strategies":tuple(strategies),"combinations":tuple(combinations),
            "independence":independence,"combo_names":names,
            "retired_strategy_ids":tuple(sorted(set(MARKET_DEFINITIONS[market]["strategies"])|set(extras["strategies"]))),
            "retired_combo_ids":tuple(sorted(set(MARKET_DEFINITIONS[market]["combinations"])|set(extras["combinations"]))),
            "production_approved":False}
