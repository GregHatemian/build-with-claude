import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

# Stub the M5 import so importing buddy_ui_stats doesn't blow up on CPython.
sys.modules["M5"] = type(sys)("M5")
sys.modules["M5"].Lcd = object()

import buddy_ui_stats

class TestFmtShort(unittest.TestCase):
    def test_under_thousand(self):
        self.assertEqual(buddy_ui_stats._fmt_short(0), "0")
        self.assertEqual(buddy_ui_stats._fmt_short(999), "999")
    def test_thousands(self):
        self.assertEqual(buddy_ui_stats._fmt_short(1234), "1.2K")
        self.assertEqual(buddy_ui_stats._fmt_short(999_999), "1000.0K")
    def test_millions(self):
        self.assertEqual(buddy_ui_stats._fmt_short(1_234_567), "1.2M")
    def test_none(self):
        self.assertEqual(buddy_ui_stats._fmt_short(None), "?")

if __name__ == "__main__":
    unittest.main()
