import unittest
from bot_profiles import BOT_PROFILES, bot_catalog, evaluate_bot

class BotProfilesTests(unittest.TestCase):
    def test_five_independent_profiles(self):
        self.assertEqual(len(BOT_PROFILES), 5)
        self.assertEqual(len({p["id"] for p in BOT_PROFILES}), 5)
        self.assertTrue(all(p["families"] for p in BOT_PROFILES))

    def test_catalog_is_disabled_by_default(self):
        items = bot_catalog()
        self.assertEqual(len(items), 5)
        self.assertTrue(all(not x["settings"]["enabled"] for x in items))
        self.assertTrue(all(x["execution_status"] == "NOT_CONNECTED" for x in items))

    def test_unknown_bot_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_bot("missing", [])

    def test_insufficient_candles_waits(self):
        result = evaluate_bot("trend", [])
        self.assertEqual(result["signal"], "WAIT")
        self.assertEqual(result["execution_status"], "NOT_CONNECTED")

if __name__ == "__main__":
    unittest.main()
