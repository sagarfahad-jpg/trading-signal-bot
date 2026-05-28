#!/usr/bin/env python3
"""
Auto-Optimizer — يشغّل Backtest ويضبط الحد الأدنى لكل أصل تلقائياً.

شغّله مرة في الأسبوع:  python3 optimizer.py
"""

import json
import os
import config
from backtest import run_backtest

THRESHOLD_FILE = os.path.join(os.path.dirname(__file__), "asset_thresholds.json")


def load_thresholds() -> dict:
    if os.path.exists(THRESHOLD_FILE):
        try:
            with open(THRESHOLD_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def optimize(days: int = 40, base_score: float = None) -> dict:
    base = base_score or config.MIN_SCORE

    print(f"🔧 Auto-Optimizer — آخر {days} يوم | الحد الأساسي: {base}\n")

    data       = run_backtest(_load_watchlist(), days=days, min_score=4.0, min_rr=1.0)
    thresholds = {}

    print(f"\n{'─'*50}")
    print(f"{'الأصل':<8} {'WR%':<8} {'إشارات':<8} {'الحد الجديد'}")
    print(f"{'─'*50}")

    for sym, stats in data["symbol_stats"].items():
        wr      = stats["win_rate"]
        signals = stats["signals"]

        if signals < 3:
            # بيانات قليلة — أضف حذر بسيط
            new_thresh = round(base + 0.5, 1)
            note = "(بيانات قليلة)"
        elif wr < 25:
            new_thresh = round(base + 2.5, 1)
            note = "⬆️⬆️ رديء جداً"
        elif wr < 40:
            new_thresh = round(base + 1.5, 1)
            note = "⬆️ دون المتوسط"
        elif wr < 55:
            new_thresh = round(base + 0.5, 1)
            note = "➡️ متوسط"
        elif wr < 70:
            new_thresh = round(base, 1)
            note = "✅ جيد"
        else:
            new_thresh = round(max(base - 0.5, 4.0), 1)
            note = "🌟 ممتاز"

        thresholds[sym] = new_thresh
        print(f"{sym:<8} {wr:<8} {signals:<8} {new_thresh}  {note}")

    with open(THRESHOLD_FILE, "w", encoding="utf-8") as f:
        json.dump(thresholds, f, ensure_ascii=False, indent=2)

    print(f"{'─'*50}")
    print(f"\n✅ تم حفظ الإعدادات في asset_thresholds.json")
    print("   البوت سيستخدمها تلقائياً في المسح القادم.\n")
    return thresholds


def _load_watchlist() -> list:
    wf = os.path.join(os.path.dirname(__file__), "watchlist.json")
    if os.path.exists(wf):
        try:
            with open(wf, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return list(config.WATCHLIST)


if __name__ == "__main__":
    optimize()
