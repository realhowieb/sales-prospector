import unittest

import trades

LAW = {"Permit #": "1", "Business": "Finnegan Law Office", "Signal": "Move-in (U&O)", "Score": 90, "Opened": "2026-11-06",
       "Description": "COM: TI/U&O for 'Finnegan Law Office'. Demo existing partition walls."}
SHOP = {"Permit #": "2", "Business": "Madewell", "Signal": "Move-in (U&O)", "Score": 90, "Opened": "2026-11-06",
        "Description": "COM: 'Madewell' Space #CC1230. TI and U&O for a 4,441sf retail space."}
LAB = {"Permit #": "3", "Business": "Google", "Signal": "Build-out (TI)", "Score": 85, "Opened": "2026-10-28",
       "Description": "Google: Tenant improvement to remodel R&D lab. Add testing PODS."}
LOBBY = {"Permit #": "4", "Business": "", "Signal": "Build-out (TI)", "Score": 85, "Opened": "2026-10-22",
         "Description": "COM: Tenant improvement including a new lobby entry, coffee bar, conference room."}


class TradeTests(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(trades.bucket_of("permits", LAW), "office")
        self.assertEqual(trades.bucket_of("permits", SHOP), "retail")
        self.assertEqual(trades.bucket_of("permits", LAB), "lab")
        self.assertEqual(trades.bucket_of("permits", LOBBY), "office")  # an office coffee bar isn't a restaurant
        self.assertEqual(trades.bucket_of("licenses", {"Type": "Hotel"}), "hotel")
        self.assertEqual(trades.bucket_of("licenses", {"Type": "Full restaurant w/ bar"}), "food")

    def test_each_trade_ranks_its_own_customers_first(self):
        rows = [LAW, SHOP, LAB]
        top = lambda t: trades.rescore("permits", rows, t)[0]["Business"]
        self.assertEqual(top("pos"), "Madewell")
        self.assertEqual(top("furniture"), "Finnegan Law Office")
        self.assertEqual(top("solar"), "Google")

    def test_rescore_adds_pitch_and_keeps_originals(self):
        out = trades.rescore("permits", [LAW], "it")
        self.assertIn("Wi-Fi", out[0]["Pitch"])
        self.assertEqual(out[0]["Fit"], "office")
        self.assertEqual(LAW["Score"], 90)  # input untouched
        self.assertEqual(trades.rescore("permits", [LAW], "nope")[0]["Pitch"],
                         trades.rescore("permits", [LAW], "security")[0]["Pitch"])  # unknown -> default

    def test_every_trade_is_complete(self):
        for key, t in trades.TRADES.items():
            self.assertIn("other", t["fit"], key)
            self.assertIn("other", t["pitch"], key)
            self.assertTrue(t["label"] and t["sells"], key)


if __name__ == "__main__":
    unittest.main()
