"""
Supabase Database Client
يخزّن الإشارات ونتائجها في قاعدة بيانات مشتركة بين Railway والـ Dashboard
"""

from __future__ import annotations

import os
import datetime
import requests
from typing import Optional, List, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from analyzer import SignalResult


def _get_secret(key: str) -> str:
    """يحاول يقرأ المتغير من st.secrets (Streamlit Cloud) أو os.getenv (Railway/Local)."""
    # أولاً: st.secrets إذا كنا داخل Streamlit
    try:
        import streamlit as st
        val = st.secrets.get(key, "")
        if val:
            return str(val)
    except Exception:
        pass
    # ثانياً: متغيرات البيئة العادية
    return os.getenv(key, "")


SUPABASE_URL = _get_secret("SUPABASE_URL")
SUPABASE_KEY = _get_secret("SUPABASE_KEY")

TABLE = "signals"


def _url() -> str:
    return SUPABASE_URL or _get_secret("SUPABASE_URL")

def _key() -> str:
    return SUPABASE_KEY or _get_secret("SUPABASE_KEY")

def is_configured() -> bool:
    return bool(_url() and _key())


def _headers(prefer: str = "return=representation") -> dict:
    h = {
        "apikey":        _key(),
        "Authorization": f"Bearer {_key()}",
        "Content-Type":  "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


# ─── Write ────────────────────────────────────────────────────────────────────

def save_signal(sig: "SignalResult") -> Optional[int]:
    """يحفظ إشارة جديدة. يُرجع ID الصف أو None عند الفشل."""
    if not is_configured():
        return None

    payload = {
        "symbol":        sig.symbol,
        "direction":     sig.direction,
        "entry_price":   round((sig.entry_low + sig.entry_high) / 2, 2),
        "entry_low":     round(sig.entry_low, 2),
        "entry_high":    round(sig.entry_high, 2),
        "stop_price":    round(sig.stop, 2),
        "target1":       round(sig.target1, 2),
        "target2":       round(sig.target2, 2),
        "score":         round(sig.score, 2),
        "rr":            round(sig.rr, 2),
        "confidence":    sig.confidence,
        "entry_type":    sig.entry_type,
        "strike":        sig.strike,
        "expiry":        sig.expiry,
        "option_price":  round(sig.option_price, 2),
        "contracts":     sig.contracts,
        "regime":        sig.regime,
        "htf_zone_tf":   sig.htf_zone_tf,
        "htf_zone_type": sig.htf_zone_type,
        "htf_direction": sig.htf_direction,
        "cisd":           sig.cisd,
        "displacement":   sig.displacement,
        "smt_divergence": sig.smt_divergence,
        "smt_direction":  sig.smt_direction,
        "max_pain":       getattr(sig, "max_pain", 0) or None,
        "call_wall":      getattr(sig, "call_wall", 0) or None,
        "put_wall":       getattr(sig, "put_wall", 0) or None,
        "pcr":            getattr(sig, "pcr", 0) or None,
        "is_manual":      False,
        "entry_filled":   False,
        "status":         "open",
    }

    try:
        r = requests.post(
            f"{_url()}/rest/v1/{TABLE}",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201):
            data = r.json()
            sig_id = data[0]["id"] if data else None
            print(f"  [db] ✅ حُفظت الإشارة #{sig_id}")
            return sig_id
        print(f"  [db] save_signal: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  [db] save_signal error: {e}")
    return None


def save_manual_signal(data: dict) -> Optional[int]:
    """يحفظ إشارة يدوية (status=open, entry_filled=False, is_manual=True)."""
    if not is_configured():
        return None
    payload = dict(data)
    payload.setdefault("is_manual",    True)
    payload.setdefault("entry_filled", False)
    payload.setdefault("status",       "open")
    try:
        r = requests.post(
            f"{_url()}/rest/v1/{TABLE}",
            headers=_headers(),
            json=payload,
            timeout=10,
        )
        if r.status_code in (200, 201):
            d = r.json()
            return d[0]["id"] if d else None
        print(f"  [db] save_manual_signal: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  [db] save_manual_signal error: {e}")
    return None


def request_exit(signal_id: int) -> bool:
    """يطلب خروجاً يدوياً فورياً (status=exit_requested) — Railway ينفّذه."""
    if not is_configured():
        return False
    try:
        r = requests.patch(
            f"{_url()}/rest/v1/{TABLE}?id=eq.{signal_id}",
            headers=_headers(prefer=""),
            json={"status": "exit_requested"},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [db] request_exit: {e}")
    return False


def get_exit_requests() -> List[Dict]:
    """يجلب الإشارات المطلوب الخروج منها يدوياً."""
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_url()}/rest/v1/{TABLE}?status=eq.exit_requested&select=*",
            headers=_headers(prefer=""),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [db] get_exit_requests: {e}")
    return []


def cancel_signal(signal_id: int) -> bool:
    """يطلب إلغاء إشارة معلّقة (status=cancel_requested) — Railway يرسل التنبيه."""
    if not is_configured():
        return False
    try:
        r = requests.patch(
            f"{_url()}/rest/v1/{TABLE}?id=eq.{signal_id}",
            headers=_headers(prefer=""),
            json={"status": "cancel_requested"},
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [db] cancel_signal: {e}")
    return False


def get_cancel_requests() -> List[Dict]:
    """يجلب الإشارات المطلوب إلغاؤها يدوياً."""
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_url()}/rest/v1/{TABLE}?status=eq.cancel_requested&select=*",
            headers=_headers(prefer=""),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [db] get_cancel_requests: {e}")
    return []


def mark_entry_filled(signal_id: int, fill_price: float = 0.0) -> bool:
    """يعلّم أن سعر الدخول قد تحقق ويسجّل سعر اللمسة الفعلي."""
    if not is_configured():
        return False
    try:
        payload = {
            "entry_filled": True,
            "entry_time":   datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if fill_price > 0:
            payload["entry_fill_price"] = round(fill_price, 2)
        r = requests.patch(
            f"{_url()}/rest/v1/{TABLE}?id=eq.{signal_id}",
            headers=_headers(prefer=""),
            json=payload,
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [db] mark_entry_filled: {e}")
    return False


def update_outcome(
    signal_id: int,
    status: str,
    outcome_price: float,
    r_multiple: float,
    exit_reason: str = "",
    duration_min: Optional[int] = None,
    max_favorable: Optional[float] = None,
    max_adverse: Optional[float] = None,
    lowest_price: Optional[float] = None,
    highest_price: Optional[float] = None,
) -> bool:
    """يحدّث نتيجة إشارة موجودة مع تفاصيل سلامة البيانات."""
    if not is_configured():
        return False

    payload = {
        "status":        status,
        "outcome_price": round(outcome_price, 2),
        "outcome_time":  datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "r_multiple":    round(r_multiple, 3),
    }
    if exit_reason:
        payload["exit_reason"] = exit_reason
    if duration_min is not None:
        payload["duration_min"] = int(duration_min)
    if max_favorable is not None:
        payload["max_favorable"] = round(max_favorable, 3)
    if max_adverse is not None:
        payload["max_adverse"] = round(max_adverse, 3)
    if lowest_price is not None:
        payload["lowest_price"] = round(lowest_price, 2)
    if highest_price is not None:
        payload["highest_price"] = round(highest_price, 2)
    try:
        r = requests.patch(
            f"{_url()}/rest/v1/{TABLE}?id=eq.{signal_id}",
            headers=_headers(prefer=""),
            json=payload,
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [db] update_outcome: {e}")
    return False


# ─── Bot Config ───────────────────────────────────────────────────────────────

CONFIG_TABLE = "bot_config"

def get_config(key: str, default: str = "") -> str:
    """يجلب قيمة إعداد من Supabase."""
    if not is_configured():
        return default
    try:
        r = requests.get(
            f"{_url()}/rest/v1/{CONFIG_TABLE}?key=eq.{key}&select=value",
            headers=_headers(prefer=""),
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            return str(data[0]["value"]) if data else default
    except Exception:
        pass
    return default


_acct_cache: dict = {"ts": 0.0, "value": None}

def get_account_size(default: float) -> float:
    """يجلب رأس المال من Supabase (cache 5 دقائق)."""
    import time as _t
    now = _t.time()
    if now - float(_acct_cache["ts"]) < 300 and _acct_cache["value"]:
        return float(_acct_cache["value"])
    try:
        val = get_config("account_size", "")
        if val:
            _acct_cache["value"] = float(val)
            _acct_cache["ts"]    = now
            return float(val)
    except Exception:
        pass
    return default


def set_config(key: str, value: str) -> bool:
    """يحفظ أو يحدّث قيمة إعداد في Supabase."""
    if not is_configured():
        return False
    try:
        import datetime as _dt
        payload = {
            "key":        key,
            "value":      str(value),
            "updated_at": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        r = requests.post(
            f"{_url()}/rest/v1/{CONFIG_TABLE}",
            headers=_headers(prefer="resolution=merge-duplicates"),
            json=payload,
            timeout=5,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


# ─── My Trades (دفتر الصفقات اليدوي) ─────────────────────────────────────────

MY_TRADES_TABLE = "my_trades"

def add_my_trade(trade: dict) -> bool:
    """يضيف صفقة يدوية جديدة."""
    if not is_configured():
        return False
    try:
        r = requests.post(
            f"{_url()}/rest/v1/{MY_TRADES_TABLE}",
            headers=_headers(),
            json=trade,
            timeout=10,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"  [db] add_my_trade: {e}")
    return False


def get_my_trades(limit: int = 500) -> List[Dict]:
    """يجلب كل الصفقات اليدوية (أحدث أولاً)."""
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_url()}/rest/v1/{MY_TRADES_TABLE}"
            f"?select=*&order=created_at.desc&limit={limit}",
            headers=_headers(prefer=""),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [db] get_my_trades: {e}")
    return []


def close_my_trade(trade_id: int, exit_price: float, pnl_dollar: float,
                   pnl_pct: float, status: str) -> bool:
    """يغلق صفقة يدوية."""
    if not is_configured():
        return False
    try:
        import datetime as _dt
        payload = {
            "exit_date":  _dt.date.today().isoformat(),
            "exit_price": round(exit_price, 2),
            "pnl_dollar": round(pnl_dollar, 2),
            "pnl_pct":    round(pnl_pct, 2),
            "status":     status,
        }
        r = requests.patch(
            f"{_url()}/rest/v1/{MY_TRADES_TABLE}?id=eq.{trade_id}",
            headers=_headers(prefer=""),
            json=payload,
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [db] close_my_trade: {e}")
    return False


def delete_my_trade(trade_id: int) -> bool:
    """يحذف صفقة يدوية."""
    if not is_configured():
        return False
    try:
        r = requests.delete(
            f"{_url()}/rest/v1/{MY_TRADES_TABLE}?id=eq.{trade_id}",
            headers=_headers(prefer=""),
            timeout=10,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        print(f"  [db] delete_my_trade: {e}")
    return False


# ─── Read ─────────────────────────────────────────────────────────────────────

def get_open_signals() -> List[Dict]:
    """يجلب كل الإشارات المفتوحة."""
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_url()}/rest/v1/{TABLE}?status=eq.open&select=*",
            headers=_headers(prefer=""),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [db] get_open_signals: {e}")
    return []


def get_all_signals(limit: int = 1000) -> List[Dict]:
    """يجلب كل الإشارات للـ Dashboard (أحدث أولاً)."""
    if not is_configured():
        return []
    try:
        r = requests.get(
            f"{_url()}/rest/v1/{TABLE}"
            f"?select=*&order=created_at.desc&limit={limit}",
            headers=_headers(prefer=""),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"  [db] get_all_signals: {e}")
    return []
