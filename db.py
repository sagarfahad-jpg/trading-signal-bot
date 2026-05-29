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
        "cisd":          sig.cisd,
        "displacement":  sig.displacement,
        "status":        "open",
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


def update_outcome(
    signal_id: int,
    status: str,
    outcome_price: float,
    r_multiple: float,
) -> bool:
    """يحدّث نتيجة إشارة موجودة."""
    if not is_configured():
        return False

    payload = {
        "status":        status,
        "outcome_price": round(outcome_price, 2),
        "outcome_time":  datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "r_multiple":    round(r_multiple, 3),
    }
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
