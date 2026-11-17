"""Readiness must fail closed; no passing checks imply real-money authorization."""
import sys
import unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"marketplace"/"backend"))
from system_readiness import owner_readiness
from multi_model_council import risk_guardian,risk_limits_from_settings

NOW=datetime(2026,11,14,18,0,tzinfo=timezone.utc)

def sample(now=NOW):
    stamp=now.timestamp()
    identity={"client_id":"owner-storage-id","revoked":False}
    snapshot={"received_at":now-timedelta(seconds=4),
        "strategy_decision":{"bar_time":stamp-65,"decision":"BUY"}}
    settings={"trading_enabled":False,"position_management":{
        "central_actions_enabled":False,"max_total_lots":.05,
        "max_drawdown_pct":3,"max_positions":3}}
    votes={"votes":{name:{"status":"READY","direction":"BUY"} for name in
                     ("Fast AI","Qwen","DeepSeek")}}
    candle=now-timedelta(minutes=1)
    return identity,snapshot,settings,votes,candle

class ReadinessTests(unittest.TestCase):
    def test_only_demo_ready_when_every_live_check_passes(self):
        result=owner_readiness(*sample(),now=NOW)
        self.assertTrue(result["ready_for_demo_validation"])
        self.assertFalse(result["ready_for_real_money"])
        self.assertFalse(result["trading_enabled"])
    def test_stale_mt5_never_reports_ready(self):
        args=list(sample())
        args[1]["received_at"]=NOW-timedelta(minutes=8)
        result=owner_readiness(*args,now=NOW)
        self.assertFalse(result["ready_for_demo_validation"])
        self.assertFalse(next(x for x in result["checks"] if x["id"]=="mt5")["ready"])
    def test_missing_deepseek_vetoes_readiness(self):
        args=list(sample())
        args[3]["votes"]["DeepSeek"]={"status":"NOT_CONFIGURED","direction":"WAIT"}
        result=owner_readiness(*args,now=NOW)
        self.assertFalse(result["ready_for_demo_validation"])
    def test_72h_weekend_tolerance_is_explicit(self):
        args=list(sample())
        args[4]=NOW-timedelta(hours=70)
        self.assertTrue(owner_readiness(*args,now=NOW)["ready_for_demo_validation"])
        args[4]=NOW-timedelta(hours=80)
        self.assertFalse(owner_readiness(*args,now=NOW)["ready_for_demo_validation"])
    def test_missing_owner_identity_blocks_demo(self):
        args=list(sample());args[0]=None
        self.assertFalse(owner_readiness(*args,now=NOW)["ready_for_demo_validation"])
    def test_owner_risk_caps_used_by_guardian(self):
        args=sample();limits=risk_limits_from_settings(args[2])
        account={"balance":1000,"equity":980,"free_margin":800}
        positions=[{"volume":0.03},{"volume":0.02}]
        self.assertFalse(risk_guardian(account,positions,limits)["approved"])
        self.assertFalse(risk_guardian(account,[{}]*3,limits)["approved"])
        self.assertFalse(risk_guardian({"balance":1000,"equity":950,"free_margin":800},[],limits)["approved"])
        self.assertTrue(risk_guardian(account,[],limits)["approved"])
    def test_owner_views_never_choose_another_users_connection(self):
        source=(ROOT/"marketplace"/"backend"/"app.py").read_text(encoding="utf-8")
        self.assertNotIn('max(CONNECTIONS.values(),key=lambda x:x["received_at"],default=None)',source)

if __name__=="__main__":unittest.main()
