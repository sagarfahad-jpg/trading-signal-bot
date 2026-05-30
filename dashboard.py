"""
Trading Signal Bot — Dashboard v2
شغّله بـ:  streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import json, os
from datetime import datetime
import pytz
import yfinance as yf
import data_client as _dc

import sys
sys.path.insert(0, os.path.dirname(__file__))
import config
from analyzer import analyze, quick_scan, get_vix, _rsi, _atr, _find_fvg, _find_order_blocks
import db

LOG_FILE = os.path.join(os.path.dirname(__file__), "signals_log.json")

# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Trading Signal Bot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* ─── عام ─── */
    [data-testid="stMetricValue"] { font-size: 2rem; }

    /* ─── Mobile Responsive ─── */
    @media (max-width: 768px) {
        [data-testid="stMetricValue"] { font-size: 1.3rem !important; }
        .card-sym  { font-size: 1.3rem !important; }
        .card-row  { font-size: .75rem !important; }
        [data-testid="column"] { min-width: 140px !important; }
        .block-container { padding: 0.5rem !important; }
    }
    .win  { color: #00c853; font-weight: 700; }
    .loss { color: #ff1744; font-weight: 700; }

    /* ─── Sidebar ─── */
    [data-testid="stSidebarContent"],
    section[data-testid="stSidebar"] > div:first-child {
        background-color: #0a0a16 !important;
    }
    section[data-testid="stSidebar"] * {
        color: #d8d8e8 !important;
    }
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] strong {
        color: #ffffff !important;
    }
    section[data-testid="stSidebar"] hr {
        border-color: #2a2a40 !important;
    }
    /* أزرار الحذف */
    section[data-testid="stSidebar"] button {
        background: #1e1e30 !important;
        border: 1px solid #2e2e4a !important;
        color: #ff5252 !important;
        border-radius: 6px !important;
    }
    section[data-testid="stSidebar"] button:hover {
        background: #2b0d0d !important;
        border-color: #ff5252 !important;
    }
    /* شريط التمرير */
    section[data-testid="stSidebar"] [role="slider"] {
        background-color: #00e676 !important;
    }
    /* حقل النص */
    section[data-testid="stSidebar"] input {
        background: #141428 !important;
        border: 1px solid #2e2e4a !important;
        color: #fff !important;
        border-radius: 6px !important;
    }
    /* ─── بطاقات قائمة الأصول ─── */
    .wl-item {
        background: #12122a;
        border: 1px solid #2a2a48;
        border-radius: 8px;
        padding: 7px 12px;
        margin: 4px 0;
        font-size: .95rem;
        font-weight: 600;
        color: #e8e8ff !important;
    }
    .card-call {
        background: linear-gradient(135deg,#0d2b18,#0a1f12);
        border: 1.5px solid #00e676; border-radius: 14px;
        padding: 18px 14px; text-align: center;
    }
    .card-put {
        background: linear-gradient(135deg,#2b0d0d,#1f0a0a);
        border: 1.5px solid #ff5252; border-radius: 14px;
        padding: 18px 14px; text-align: center;
    }
    .card-sym  { font-size:1.9rem; font-weight:900; margin:0; }
    .card-dir  { font-size:1.1rem; margin:4px 0 12px; }
    .card-row  { display:flex; justify-content:space-between;
                 font-size:.85rem; color:#ccc; margin:3px 0; }
    .card-val  { font-weight:700; color:#fff; }
    .score-bar-wrap { background:#1e1e2e; border-radius:8px;
                      height:8px; margin:10px 0 4px; overflow:hidden; }
    .score-bar { height:8px; border-radius:8px; }
</style>
<script>
(function() {
    // لا تحدّث الصفحة إذا كان URL يحتوي على ?deep=
    if (!window.location.search.includes('deep=')) {
        setTimeout(function() { window.location.reload(); }, 60000);
    }
})();
</script>
""", unsafe_allow_html=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_log() -> list:
    # ── أولاً: Supabase (مشترك مع Railway) ───────────────────────────────────
    if db.is_configured():
        try:
            raw = db.get_all_signals(limit=500)
            if raw:
                result = []
                _status_map = {
                    "hit_t2":  "WIN_T2 ✅✅",
                    "hit_t1":  "WIN_T1 ✅",
                    "stopped": "LOSS ❌",
                    "expired": "expired",
                    "open":    None,
                }
                for r in raw:
                    result.append({
                        "id"             : r.get("id"),
                        "timestamp"      : str(r.get("created_at", ""))[:19].replace("T", " "),
                        "symbol"         : r.get("symbol", ""),
                        "direction"      : r.get("direction", ""),
                        "confidence"     : r.get("confidence", ""),
                        "score"          : r.get("score", 0),
                        "rr"             : r.get("rr", 0),
                        "vix"            : 0,
                        "mtf_score"      : 0,
                        "entry_low"      : r.get("entry_price", 0),
                        "entry_high"     : r.get("entry_price", 0),
                        "suggested_entry": r.get("entry_price", 0),
                        "stop"           : r.get("stop_price", 0),
                        "target1"        : r.get("target1", 0),
                        "target2"        : r.get("target2", 0),
                        "entry_type"     : r.get("entry_type", ""),
                        "option_price"   : r.get("option_price", 0),
                        "contracts"      : r.get("contracts", 0),
                        "sent"           : True,
                        "outcome"        : _status_map.get(r.get("status", "open")),
                        "notified"       : True,
                    })
                return result
        except Exception:
            pass

    # ── ثانياً: ملف محلي (fallback) ──────────────────────────────────────────
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


@st.cache_data(ttl=60)
def scan_all(min_score: float) -> pd.DataFrame:
    rows = []
    for sym in load_watchlist():
        s = quick_scan(sym)
        if s:
            is_opportunity = s["score"] >= min_score and s["rr"] >= 1.5
            rows.append({
                "الأصل"  : sym,
                "الاتجاه": "🟢 CALL" if s["direction"] == "call" else "🔴 PUT",
                "التقييم": round(s["score"],    1),
                "RSI"    : round(s["rsi"],       1),
                "R:R"    : round(s["rr"],        2),
                "السعر"  : round(s["price"],     2),
                "دخول"   : f"{s['entry_low']:.2f}–{s['entry_high']:.2f}",
                "وقف"    : round(s["stop"],      2),
                "هدف 1"  : round(s["target1"],   2),
                "هدف 2"  : round(s["target2"],   2),
                "OB"     : "✅" if (s["bull_ob"]    or s["bear_ob"])    else "—",
                "FVG"    : "✅" if (s["bull_fvg"]   or s["bear_fvg"])   else "—",
                "Div"    : "✅" if (s.get("bull_div")  or s.get("bear_div"))  else "—",
                "Break"  : "✅" if (s.get("bull_break") or s.get("bear_break")) else "—",
                "Sweep"  : "✅" if (s.get("bull_sweep") or s.get("bear_sweep")) else "—",
                "Regime" : {"bull": "📈 Bull", "bear": "📉 Bear", "neutral": "↔ Neutral"}.get(s.get("regime", ""), "—"),
                "فرصة؟"  : "✅" if is_opportunity else "❌",
                "_score_raw" : s["score"],
                "_dir_raw"   : s["direction"],
                "_entry_low" : s["entry_low"],
                "_entry_high": s["entry_high"],
                "_stop"      : s["stop"],
                "_t1"        : s["target1"],
                "_t2"        : s["target2"],
                "_regime"    : s.get("regime", ""),
            })
        else:
            rows.append({
                "الأصل": sym, "الاتجاه": "—", "التقييم": 0,
                "RSI": "—", "R:R": 0, "السعر": 0, "دخول": "—",
                "وقف": "—", "هدف 1": "—", "هدف 2": "—",
                "OB": "—", "FVG": "—",
                "Div": "—", "Break": "—", "Sweep": "—",
                "Regime": "—", "فرصة؟": "❌",
                "_score_raw": 0, "_dir_raw": "", "_entry_low": 0,
                "_entry_high": 0, "_stop": 0, "_t1": 0, "_t2": 0,
                "_regime": "",
            })
    df = pd.DataFrame(rows)
    return df.sort_values("التقييم", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=60)
def fetch_chart(symbol: str):
    return _dc.get_bars(symbol, '5m', '2d')


def outcome_stats(log: list):
    sent = [e for e in log if e.get("sent")]
    wins   = [e for e in sent if "WIN"  in str(e.get("outcome", ""))]
    losses = [e for e in sent if "LOSS" in str(e.get("outcome", ""))]
    total_decided = len(wins) + len(losses)
    win_rate = round(len(wins) / total_decided * 100) if total_decided > 0 else 0
    return len(sent), len(wins), len(losses), win_rate


def get_market_status():
    """يُرجع (is_open, label, minutes_left)"""
    et  = pytz.timezone(config.TIMEZONE)
    now = datetime.now(et)
    if now.weekday() >= 5:
        return False, "مغلق — عطلة نهاية الأسبوع", None
    open_t  = now.replace(hour=9,  minute=35, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=45, second=0, microsecond=0)
    if now < open_t:
        mins = int((open_t - now).total_seconds() / 60)
        h, m = divmod(mins, 60)
        label = f"مغلق — يفتح بعد {h}س {m}د" if h else f"مغلق — يفتح بعد {m} دقيقة"
        return False, label, mins
    if now > close_t:
        return False, "مغلق — أغلق السوق اليوم", None
    mins = int((close_t - now).total_seconds() / 60)
    h, m = divmod(mins, 60)
    label = f"مفتوح ✅ — يغلق بعد {h}س {m}د" if h else f"مفتوح ✅ — يغلق بعد {m} دقيقة"
    return True, label, mins


# ─── Watchlist helpers ────────────────────────────────────────────────────────

WATCHLIST_FILE = os.path.join(os.path.dirname(__file__), "watchlist.json")

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return list(config.WATCHLIST)

def save_watchlist(wl: list):
    with open(WATCHLIST_FILE, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)


# ─── Password Protection ──────────────────────────────────────────────────────
try:
    _pwd_required = st.secrets.get("DASHBOARD_PASSWORD", "")
except Exception:
    _pwd_required = ""
if _pwd_required:
    if not st.session_state.get("authenticated"):
        st.title("🔒 Trading Signal Bot")
        st.markdown("---")
        _col1, _col2, _col3 = st.columns([1, 2, 1])
        with _col2:
            _pwd_input = st.text_input("كلمة المرور", type="password", placeholder="أدخل كلمة المرور...")
            if st.button("دخول 🚀", use_container_width=True):
                if _pwd_input == _pwd_required:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("❌ كلمة المرور غير صحيحة")
        st.stop()

# ─── Session state: استرجاع deep_sym من URL بعد الـ refresh ──────────────────
if "deep_sym" not in st.session_state:
    st.session_state.deep_sym = st.query_params.get("deep", None)

# ─── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("⚙️ إعدادات العرض")
if "min_score" not in st.session_state:
    _remote = db.get_config("min_score", str(config.MIN_SCORE))
    try:
        st.session_state["min_score"] = float(_remote)
    except Exception:
        st.session_state["min_score"] = config.MIN_SCORE

min_score_ui = st.sidebar.slider(
    "حد الفرصة (Score)", 0.0, 15.0,
    st.session_state["min_score"], 0.5,
    key="min_score",
)
if st.sidebar.button("💾 حفظ للبوت", use_container_width=True):
    if db.set_config("min_score", str(min_score_ui)):
        st.sidebar.success(f"✅ تم الحفظ — البوت سيطبّق {min_score_ui} في المسح القادم")
    else:
        st.sidebar.error("❌ فشل الحفظ")

# ── رأس المال ─────────────────────────────────────────────────────────────────
if "account_size" not in st.session_state:
    _acc_remote = db.get_config("account_size", str(config.ACCOUNT_SIZE))
    try:
        st.session_state["account_size"] = float(_acc_remote)
    except Exception:
        st.session_state["account_size"] = config.ACCOUNT_SIZE

account_size_ui = st.sidebar.number_input(
    "💰 رأس المال ($)", min_value=100.0, max_value=10_000_000.0,
    value=st.session_state["account_size"], step=100.0,
    key="account_size",
)
st.sidebar.caption(f"المخاطرة لكل صفقة: ${account_size_ui * config.RISK_PCT:.0f} ({config.RISK_PCT*100:.0f}%)")
if st.sidebar.button("💾 حفظ رأس المال", use_container_width=True):
    if db.set_config("account_size", str(account_size_ui)):
        st.sidebar.success(f"✅ تم الحفظ — رأس المال ${account_size_ui:.0f}")
    else:
        st.sidebar.error("❌ فشل الحفظ")

current_wl  = load_watchlist()
selected_sym = st.sidebar.selectbox("رسم بياني للأصل", current_wl)
show_levels  = st.sidebar.checkbox("عرض مستويات الإشارة على الرسم", value=True)

st.sidebar.divider()
st.sidebar.subheader("📋 قائمة الأصول")

threshold_file = os.path.join(os.path.dirname(__file__), "asset_thresholds.json")
_thresholds = {}
try:
    with open(threshold_file, encoding="utf-8") as _f:
        _thresholds = json.load(_f)
except Exception:
    pass

for sym in list(current_wl):
    c1s, c2s = st.sidebar.columns([4, 1])
    thresh = _thresholds.get(sym, config.MIN_SCORE)
    c1s.markdown(
        f'<div class="wl-item">'
        f'<span style="color:#fff;">{sym}</span>'
        f'<span style="color:#888;font-size:.75rem;">حد {thresh}</span>'
        f'</div>',
        unsafe_allow_html=True
    )
    if c2s.button("🗑", key=f"del_{sym}"):
        current_wl.remove(sym)
        save_watchlist(current_wl)
        st.rerun()

new_sym = st.sidebar.text_input("➕ أضف أصلاً جديداً (مثال: NVDA)", "").upper().strip()
if st.sidebar.button("إضافة") and new_sym and new_sym not in current_wl:
    current_wl.append(new_sym)
    save_watchlist(current_wl)
    st.rerun()

st.sidebar.divider()
st.sidebar.caption("🔄 الصفحة تتحدث تلقائياً كل 60 ثانية")
if st.sidebar.button("🔄 تحديث الآن"):
    st.cache_data.clear()
    st.rerun()


# ─── Header ───────────────────────────────────────────────────────────────────

st.title("🤖 Trading Signal Bot")
st.caption(f"آخر تحديث: {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")

# ── Market Status Banner ───────────────────────────────────────────────────────

is_open, market_label, mins_left = get_market_status()
vix = get_vix()

banner_color = "#0d2b18" if is_open else "#1a1a1a"
border_color = "#00e676" if is_open else "#ff5252"
status_icon  = "🟢" if is_open else "🔴"

col_status, col_vix = st.columns([3, 1])

with col_status:
    _premarket_extra = (
        f'&nbsp;|&nbsp; <b style="color:{border_color};">يبدأ الفحص الأول بعد {mins_left} دقيقة</b>'
        if not is_open and mins_left else ''
    )
    st.markdown(
        f'<div style="background:{banner_color}; border:1.5px solid {border_color};'
        f' border-radius:12px; padding:16px 20px; margin-bottom:8px;">'
        f'<p style="font-size:1.6rem; font-weight:900; color:{border_color}; margin:0 0 4px 0;">'
        f'{status_icon} السوق — {market_label}</p>'
        f'<p style="color:#aaa; font-size:.9rem; margin:0;">'
        f'توقيت نيويورك (ET) &nbsp;|&nbsp; الفحص كل {config.SCAN_INTERVAL_MINUTES} دقيقة'
        f'{_premarket_extra}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

with col_vix:
    vix_color = "#00c853" if vix < 20 else ("#ffd740" if vix < 28 else "#ff1744")
    vix_label = "هادئ" if vix < 20 else ("متوسط" if vix < 28 else "مرتفع ⚠️")
    fig_vix = go.Figure(go.Indicator(
        mode="gauge+number",
        value=vix,
        number=dict(font=dict(color=vix_color, size=32)),
        gauge=dict(
            axis=dict(range=[0, 45], tickcolor="white", tickfont=dict(color="white")),
            bar=dict(color=vix_color, thickness=0.35),
            bgcolor="#1e1e2e",
            steps=[
                dict(range=[0,  20], color="#0d2b18"),
                dict(range=[20, 28], color="#2b2700"),
                dict(range=[28, 45], color="#2b0d0d"),
            ],
            threshold=dict(line=dict(color=vix_color, width=3), thickness=0.75, value=vix),
        ),
        title=dict(text=f"VIX — {vix_label}", font=dict(color="white", size=13)),
    ))
    fig_vix.update_layout(
        height=160, margin=dict(l=10, r=10, t=30, b=0),
        paper_bgcolor="rgba(0,0,0,0)", font_color="white",
    )
    st.plotly_chart(fig_vix, width="stretch")

st.divider()

# ─── KPI Row ──────────────────────────────────────────────────────────────────

log  = load_log()
total_sent, wins, losses, win_rate = outcome_stats(log)

# ── إجمالي R ─────────────────────────────────────────────────────────────────
_decided = [e for e in log if "WIN" in str(e.get("outcome","")) or "LOSS" in str(e.get("outcome",""))]
_total_r  = 0.0
for _e in _decided:
    _o  = str(_e.get("outcome",""))
    _rr = float(_e.get("rr", 1.5) or 1.5)
    if   "WIN_T2" in _o: _total_r += _rr
    elif "WIN_T1" in _o: _total_r += _rr * 0.5
    elif "LOSS"   in _o: _total_r -= 1.0
_total_r = round(_total_r, 2)

# ── هذا الأسبوع ───────────────────────────────────────────────────────────────
import datetime as _dt
_et       = pytz.timezone(config.TIMEZONE)
_now_et   = datetime.now(_et)
_week_start = (_now_et - _dt.timedelta(days=_now_et.weekday())).strftime("%Y-%m-%d")
_week_sigs  = [e for e in log if e.get("timestamp","") >= _week_start]

# ── مفتوحة الآن ──────────────────────────────────────────────────────────────
_open_now = [e for e in log if e.get("outcome") is None and e.get("sent")]

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("📤 إجمالي الإشارات", total_sent)
k2.metric("✅ فوز", wins, delta=f"{win_rate}% معدل الفوز" if total_sent else None)
k3.metric("❌ خسارة", losses)
k4.metric("🔵 مفتوحة الآن", len(_open_now),
          delta=f"{len(_week_sigs)} هذا الأسبوع")
_r_color = "normal" if _total_r >= 0 else "inverse"
k5.metric("💹 إجمالي R",
          f"{'+' if _total_r >= 0 else ''}{_total_r}R",
          delta=f"متوسط {round(_total_r/len(_decided),2) if _decided else 0}R/صفقة")

st.divider()

# ─── Tabs ─────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📊 مسح الأصول", "📋 سجل الإشارات", "📈 الرسم البياني", "🧪 Backtest", "🏆 أداء الإشارات", "💼 صفقاتي"])


# ── Tab 1: Asset Scanner ───────────────────────────────────────────────────────
with tab1:
    with st.spinner("جاري تحليل الأصول..."):
        df_scan = scan_all(min_score_ui)

    # ── Heatmap ──────────────────────────────────────────────────────────────
    st.subheader("🌡️ خريطة حرارية للأصول")

    df_heat = df_scan[df_scan["التقييم"] > 0].copy()
    if not df_heat.empty:
        # بناء matrix: الأصول × المؤشرات
        heat_syms   = df_heat["الأصل"].tolist()
        heat_scores = df_heat["التقييم"].tolist()
        heat_rsi    = [float(r) if str(r).replace('.','').isdigit() else 50 for r in df_heat["RSI"]]
        heat_rr     = df_heat["R:R"].tolist()

        # تطبيع RSI: قرب 30 = فرصة كول، قرب 70 = فرصة بوت → نحوّله لقيمة محايدة
        heat_rsi_norm = [abs(r - 50) / 50 * 10 for r in heat_rsi]

        z_matrix  = [heat_scores, heat_rsi_norm, heat_rr]
        y_labels  = ["التقييم", "RSI (بُعد عن 50)", "R:R"]

        fig_heat = go.Figure(go.Heatmap(
            z=z_matrix,
            x=heat_syms,
            y=y_labels,
            colorscale=[
                [0.0,  "#2b0d0d"],
                [0.35, "#5c2a00"],
                [0.6,  "#1a3a1a"],
                [1.0,  "#00c853"],
            ],
            text=[[f"{v:.1f}" for v in row] for row in z_matrix],
            texttemplate="%{text}",
            textfont=dict(size=13, color="white"),
            showscale=True,
            colorbar=dict(tickfont=dict(color="white"), title=dict(text="قوة", font=dict(color="white"))),
        ))
        fig_heat.update_layout(
            height=220,
            paper_bgcolor="#0f0f1a", plot_bgcolor="#0f0f1a",
            font_color="white",
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(side="top", tickfont=dict(size=13, color="white")),
            yaxis=dict(tickfont=dict(size=11, color="white")),
        )
        st.plotly_chart(fig_heat, width="stretch")
    else:
        st.info("لا تتوفر بيانات كافية للخريطة الحرارية.")

    st.divider()

    # ── Smart Filter Bar ──────────────────────────────────────────────────────
    sf1, sf2, sf3 = st.columns([3, 2, 1])
    sf_dir = sf1.radio(
        "🔀 الاتجاه",
        ["الكل 📊", "CALL 🟢", "PUT 🔴"],
        horizontal=True, key="sf_dir",
    )
    sf_reg = sf2.selectbox(
        "🌐 Regime",
        ["الكل", "Bull 📈", "Bear 📉", "Neutral ↔"],
        key="sf_reg",
    )
    sf_opp = sf3.checkbox("✅ فرص فقط", value=False, key="sf_opp")

    df_filtered = df_scan.copy()
    if sf_dir == "CALL 🟢":
        df_filtered = df_filtered[df_filtered["_dir_raw"] == "call"]
    elif sf_dir == "PUT 🔴":
        df_filtered = df_filtered[df_filtered["_dir_raw"] == "put"]
    _regime_rev = {"Bull 📈": "bull", "Bear 📉": "bear", "Neutral ↔": "neutral"}
    if sf_reg != "الكل":
        df_filtered = df_filtered[df_filtered["_regime"] == _regime_rev.get(sf_reg, "")]
    if sf_opp:
        df_filtered = df_filtered[df_filtered["فرصة؟"] == "✅"]
    df_filtered = df_filtered.reset_index(drop=True)

    # ── Opportunity Cards ─────────────────────────────────────────────────────
    ready = df_filtered[df_filtered["فرصة؟"] == "✅"].head(5)

    if "deep_sym" not in st.session_state:
        st.session_state.deep_sym = None

    if not ready.empty:
        st.subheader(f"🎯 الفرص الجاهزة الآن ({len(ready)})")
        card_cols = st.columns(min(len(ready), 5))

        for i, (_, row) in enumerate(ready.iterrows()):
            is_call   = "CALL" in str(row["الاتجاه"])
            dir_color = "#00e676" if is_call else "#ff5252"
            dir_ar    = "كول 🟢" if is_call else "بوت 🔴"
            card_cls  = "card-call" if is_call else "card-put"
            score_pct = min(row["التقييم"] / 15 * 100, 100)
            mid_entry = round((row["_entry_low"] + row["_entry_high"]) / 2, 2)
            regime_color = {"bull": "#00e676", "bear": "#ff5252", "neutral": "#888"}.get(
                str(row.get("_regime", "")), "#888")
            regime_label = {"bull": "📈 Bull", "bear": "📉 Bear", "neutral": "↔ Neutral"}.get(
                str(row.get("_regime", "")), "—")

            with card_cols[i]:
                st.markdown(f"""
                <div class="{card_cls}">
                    <p class="card-sym" style="color:{dir_color};">{row['الأصل']}</p>
                    <p class="card-dir" style="color:{dir_color};">{dir_ar}</p>
                    <div class="score-bar-wrap">
                        <div class="score-bar"
                             style="width:{score_pct:.0f}%;background:{dir_color};"></div>
                    </div>
                    <p style="font-size:.75rem;color:#aaa;margin:0 0 10px;">
                        تقييم {row['التقييم']:.1f} ★
                    </p>
                    <div class="card-row">
                        <span>منطقة الدخول</span>
                        <span class="card-val" style="font-size:.78rem;">{row['_entry_low']:.2f} – {row['_entry_high']:.2f}</span>
                    </div>
                    <div class="card-row">
                        <span>دخول مقترح</span>
                        <span class="card-val">{mid_entry}</span>
                    </div>
                    <div class="card-row">
                        <span>هدف ١</span>
                        <span class="card-val" style="color:#00e676;">{row['هدف 1']}</span>
                    </div>
                    <div class="card-row">
                        <span>هدف ٢</span>
                        <span class="card-val" style="color:#00e676;">{row['هدف 2']}</span>
                    </div>
                    <div class="card-row">
                        <span>وقف</span>
                        <span class="card-val" style="color:#ff5252;">{row['وقف']}</span>
                    </div>
                    <div class="card-row">
                        <span>R:R</span>
                        <span class="card-val">{row['R:R']}</span>
                    </div>
                    <div class="card-row">
                        <span>Regime</span>
                        <span class="card-val" style="color:{regime_color};">{regime_label}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                # زر التحليل العميق
                btn_label = "⏳ جاري..." if st.session_state.deep_sym == row["الأصل"] else "🔍 تحليل عميق"
                if st.button(btn_label, key=f"deep_{row['الأصل']}", use_container_width=True):
                    st.session_state.deep_sym = row["الأصل"]
                    st.query_params["deep"] = row["الأصل"]
                    st.rerun()

    else:
        st.info("لا توجد فرص تستوفي الشروط حالياً — انتظر الفحص القادم.")

    # ── Deep Analysis Panel ───────────────────────────────────────────────────
    if st.session_state.deep_sym:
        sym = st.session_state.deep_sym
        st.divider()
        col_h, col_x = st.columns([6, 1])
        col_h.subheader(f"🔬 تحليل عميق — {sym}")
        if col_x.button("✕ إغلاق", key="close_deep"):
            st.session_state.deep_sym = None
            st.query_params.clear()
            st.rerun()

        with st.spinner(f"جاري جلب البيانات وتحليل العقود لـ {sym}... (قد يستغرق 10-20 ثانية)"):
            sig = analyze(sym, min_score=0, min_rr=0, require_mtf=False)

        if sig is None:
            st.warning(f"لم يتوفر تحليل كافٍ لـ {sym} حالياً.")
        else:
            dir_ar      = "كول 🟢"  if sig.direction == "call" else "بوت 🔴"
            conf_ar     = "عالية 🟢" if sig.confidence == "high" else "متوسطة 🟡"
            mid_e       = round((sig.entry_low + sig.entry_high) / 2, 2)
            expiry_fmt  = f"{sig.expiry[:4]}-{sig.expiry[4:6]}-{sig.expiry[6:]}" if len(sig.expiry) == 8 else sig.expiry
            expiry_type = "0DTE ⚡" if sig.is_scalp else "أسبوعي 📅"
            premium_str = f"~${sig.option_price:.2f}" if sig.option_price > 0 else "—"

            # ── صف ١: ملخص
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("الاتجاه",  dir_ar)
            d2.metric("التقييم",  f"{sig.score:.1f} ★")
            d3.metric("الثقة",    conf_ar)
            d4.metric("R:R",      f"{sig.rr:.2f}")
            d5.metric("MTF",      f"{sig.mtf_score}/2")

            # ── صف ٢: مستويات التداول
            st.caption("📍 مستويات التداول")
            e1, e2, e3, e4, e5, e6 = st.columns(6)
            e1.metric("نوع الدخول",    sig.entry_type)
            e2.metric("منطقة الدخول", f"{sig.entry_low:.2f} – {sig.entry_high:.2f}")
            e3.metric("دخول مقترح ◀️", str(mid_e))
            e4.metric("🛑 وقف",        f"{sig.stop:.2f}")
            e5.metric("✅ هدف ١",       f"{sig.target1:.2f}")
            e6.metric("✅✅ هدف ٢",      f"{sig.target2:.2f}")

            st.divider()

            # ── صف ٣: تفاصيل العقد + Greeks
            st.caption("📋 تفاصيل العقد")
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Expiry",        f"{expiry_fmt} ({expiry_type})")
            c2.metric("Strike",        f"{sig.strike:.1f}")
            c3.metric("نوع العقد",     dir_ar)
            c4.metric("💰 Premium",    premium_str)
            c5.metric("📦 عدد العقود", f"{sig.contracts}")
            c6.metric("🏦 مخاطرة",     f"${sig.contracts * sig.option_price * 100:.0f}" if sig.option_price > 0 else "—")

            # ── HTF Zone Banner ───────────────────────────────────────────────
            if sig.htf_zone_tf:
                tf_labels = {'1h': '1H', '4h': '4H', 'daily': 'Daily'}
                htf_label = f"{tf_labels.get(sig.htf_zone_tf, sig.htf_zone_tf)} {sig.htf_zone_type}"
                dir_z_ar  = 'Demand 🟢' if sig.htf_direction == 'demand' else 'Supply 🔴'
                if sig.cisd:
                    st.success(f"🏛️ HTF Zone: **{htf_label}** ({dir_z_ar})  |  ⚡ **CISD مؤكَّد** — تحوّل هيكلي")
                elif sig.displacement:
                    st.success(f"🏛️ HTF Zone: **{htf_label}** ({dir_z_ar})  |  💪 **Displacement** — تدخل مؤسسي")
                else:
                    st.info(f"🏛️ HTF Zone: **{htf_label}** ({dir_z_ar})  |  ⏳ السعر في المنطقة — انتظار تأكيد")
            else:
                st.warning("⚠️ السعر ليس في منطقة HTF محددة — إشارة خارج النطاق")

            # ── صف ٤: Greeks + VWAP + Regime
            if sig.delta != 0 or sig.vwap > 0:
                st.caption("📐 Greeks & Market Context")
                g1, g2, g3, g4, g5 = st.columns(5)
                g1.metric("Delta Δ",  f"{sig.delta:.2f}" if sig.delta else "—")
                g2.metric("IV",       f"{sig.iv:.1f}%" if sig.iv else "—")
                g3.metric("Theta Θ",  f"{sig.theta:.3f}" if sig.theta else "—")
                g4.metric("VWAP",     f"{sig.vwap:.2f}" if sig.vwap else "—")
                regime_map = {'bull': '📈 صاعد', 'bear': '📉 هابط', 'neutral': '↔ محايد'}
                g5.metric("Regime",   regime_map.get(sig.regime, "—"))

    st.divider()

    # ── Full Table ────────────────────────────────────────────────────────────
    st.subheader("📋 مسح كامل للأصول")

    display_cols = ["الأصل","الاتجاه","التقييم","RSI","R:R","السعر","دخول","وقف","هدف 1","هدف 2","OB","FVG","Div","Break","Sweep","Regime","فرصة؟"]
    df_display   = df_filtered[display_cols]

    fmt = {
        "التقييم": "{:.1f}",
        "RSI"    : "{:.1f}",
        "R:R"    : "{:.2f}",
        "السعر"  : "{:.2f}",
        "وقف"    : "{:.2f}",
        "هدف 1"  : "{:.2f}",
        "هدف 2"  : "{:.2f}",
    }

    # تحويل الأعمدة لأرقام لتطبيق الـ gradient
    df_num = df_display.copy()
    df_num["التقييم"] = pd.to_numeric(df_num["التقييم"], errors="coerce")
    df_num["RSI"]     = pd.to_numeric(df_num["RSI"],     errors="coerce")
    df_num["R:R"]     = pd.to_numeric(df_num["R:R"],     errors="coerce")

    styled = (
        df_num.style
        # ── تدرج لوني على التقييم: أحمر(0) → أصفر(5) → أخضر(10)
        .background_gradient(
            subset=["التقييم"],
            cmap="RdYlGn",
            vmin=2, vmax=9,
        )
        # ── تدرج لوني على R:R: أحمر(1) → أخضر(4+)
        .background_gradient(
            subset=["R:R"],
            cmap="RdYlGn",
            vmin=1.0, vmax=4.0,
        )
        # ── RSI: الأطراف خطر، المنتصف آمن (مقلوب عن المنتصف)
        .background_gradient(
            subset=["RSI"],
            cmap="RdYlGn_r",
            vmin=25, vmax=75,
        )
        # ── تمييز صفوف الفرص بحد جانبي فقط (بدون تغيير الخلفية)
        .apply(lambda row: [
            "border-right: 4px solid #00e676; font-weight:600"
            if row["فرصة؟"] == "✅" and "CALL" in str(row["الاتجاه"])
            else (
                "border-right: 4px solid #ff5252; font-weight:600"
                if row["فرصة؟"] == "✅" and "PUT" in str(row["الاتجاه"])
                else ""
            )
            for _ in row
        ], axis=1)
        .format(fmt, na_rep="—")
    )

    st.dataframe(styled, width="stretch", height=400)


# ── Tab 2: Signal History ──────────────────────────────────────────────────────
with tab2:
    st.subheader("سجل الإشارات المرسلة")
    if not log:
        st.info("لا توجد إشارات مسجّلة بعد.")
    else:
        df_log = pd.DataFrame(log[::-1])

        # عمود الدخول = متوسط entry_low و entry_high
        if "entry_low" in df_log.columns and "entry_high" in df_log.columns:
            df_log["دخول"] = ((pd.to_numeric(df_log["entry_low"], errors="coerce") +
                               pd.to_numeric(df_log["entry_high"], errors="coerce")) / 2).round(2)
        elif "suggested_entry" in df_log.columns:
            df_log["دخول"] = pd.to_numeric(df_log["suggested_entry"], errors="coerce").round(2)

        cols_show = ["timestamp","symbol","direction","confidence","score","rr",
                     "دخول","stop","target1","target2","outcome"]
        cols_show = [c for c in cols_show if c in df_log.columns]
        df_log = df_log[cols_show].rename(columns={
            "timestamp" : "الوقت",
            "symbol"    : "الأصل",
            "direction" : "الاتجاه",
            "confidence": "الثقة",
            "score"     : "التقييم",
            "rr"        : "R:R",
            "stop"      : "وقف",
            "target1"   : "هدف 1",
            "target2"   : "هدف 2",
            "outcome"   : "النتيجة",
        })

        # تحويل الأعمدة الرقمية
        for _c in ["التقييم","R:R","دخول","وقف","هدف 1","هدف 2"]:
            if _c in df_log.columns:
                df_log[_c] = pd.to_numeric(df_log[_c], errors="coerce")

        def color_outcome(val):
            if val and "WIN"  in str(val): return "color: #00c853; font-weight:bold"
            if val and "LOSS" in str(val): return "color: #ff1744; font-weight:bold"
            return ""

        st.dataframe(
            df_log.style
                .map(color_outcome, subset=["النتيجة"])
                .format({
                    "التقييم": "{:.1f}",
                    "R:R"    : "{:.2f}",
                    "دخول"   : "{:.2f}",
                    "وقف"    : "{:.2f}",
                    "هدف 1"  : "{:.2f}",
                    "هدف 2"  : "{:.2f}",
                }, na_rep="—"),
            width="stretch",
            height=500,
        )

        if wins + losses > 0:
            st.subheader("📊 الأداء الإجمالي")

            # ── Pie + KPIs ────────────────────────────────────────────────────
            fig_pie = go.Figure(go.Pie(
                labels=["فوز ✅", "خسارة ❌"],
                values=[wins, losses],
                hole=0.55,
                marker_colors=["#00c853","#ff1744"],
            ))
            fig_pie.update_layout(
                height=300, paper_bgcolor="rgba(0,0,0,0)",
                font_color="white", showlegend=True,
                annotations=[{"text": f"{win_rate}%", "font_size": 28,
                               "showarrow": False, "font_color": "white"}],
            )
            ca, cb, cc = st.columns([1,1,1])
            with cb:
                st.plotly_chart(fig_pie, width="stretch")

            # ── منحنى الأرباح التراكمية (R-Multiples) ────────────────────────
            st.subheader("📈 منحنى الأرباح التراكمية")
            st.caption("WIN_T2 = R:R كامل  |  WIN_T1 = نصف R:R  |  LOSS = −1R")

            decided = [e for e in log if
                       "WIN" in str(e.get("outcome", "")) or
                       "LOSS" in str(e.get("outcome", ""))]
            if decided:
                r_rows = []
                for e in decided:
                    outcome = str(e.get("outcome", ""))
                    rr_val  = float(e.get("rr", 1.5) or 1.5)
                    if   "WIN_T2" in outcome: r_val = rr_val
                    elif "WIN_T1" in outcome: r_val = rr_val * 0.5
                    elif "LOSS"   in outcome: r_val = -1.0
                    else: continue
                    r_rows.append({
                        "idx"   : e["timestamp"][:16],
                        "symbol": e.get("symbol", ""),
                        "R"     : r_val,
                    })

                if r_rows:
                    df_r = pd.DataFrame(r_rows)
                    df_r["cum_R"] = df_r["R"].cumsum()
                    total_r      = round(float(df_r["R"].sum()), 2)
                    clr_total    = "#00c853" if total_r >= 0 else "#ff1744"

                    fig_eq = go.Figure()
                    # منطقة خضراء/حمراء حسب الاتجاه
                    fig_eq.add_trace(go.Scatter(
                        x=list(range(1, len(df_r) + 1)),
                        y=df_r["cum_R"].tolist(),
                        name="R التراكمي",
                        line=dict(color=clr_total, width=2.5),
                        fill="tozeroy",
                        fillcolor=f"{'rgba(0,200,83,0.10)' if total_r >= 0 else 'rgba(255,23,68,0.10)'}",
                        text=[f"{r['symbol']}: {'+' if r['R'] > 0 else ''}{r['R']:.1f}R"
                              for _, r in df_r.iterrows()],
                        hoverinfo="text+y",
                    ))
                    fig_eq.add_hline(y=0, line_dash="dash",
                                     line_color="#555", opacity=0.9)
                    fig_eq.update_layout(
                        title=dict(
                            text=(f"إجمالي الأداء: "
                                  f"<b style='color:{clr_total};'>"
                                  f"{'+' if total_r >= 0 else ''}{total_r}R</b>"
                                  f"   ({len(r_rows)} إشارة)"),
                            font=dict(color="white", size=14),
                        ),
                        paper_bgcolor="#0f0f1a", plot_bgcolor="#0f0f1a",
                        font_color="white", height=280,
                        xaxis=dict(gridcolor="#1e1e2e",
                                   title=dict(text="رقم الإشارة", font=dict(color="#aaa"))),
                        yaxis=dict(gridcolor="#1e1e2e",
                                   title=dict(text="R Multiples", font=dict(color="#aaa"))),
                        margin=dict(l=10, r=10, t=50, b=10),
                        showlegend=False,
                    )
                    st.plotly_chart(fig_eq, width="stretch")
            else:
                st.info("📭 لا توجد نتائج WIN/LOSS مسجّلة بعد — المنحنى يظهر تلقائياً.")

            # ── نسبة الفوز حسب نوع الدخول ────────────────────────────────────
            et_decided = [e for e in log
                          if e.get("entry_type")
                          and ("WIN" in str(e.get("outcome", ""))
                               or "LOSS" in str(e.get("outcome", "")))]
            if et_decided:
                st.subheader("🏆 أداء كل نوع دخول")
                et_stats: dict = {}
                for e in et_decided:
                    et = e["entry_type"]
                    if et not in et_stats:
                        et_stats[et] = {"wins": 0, "losses": 0}
                    if "WIN" in str(e.get("outcome", "")):
                        et_stats[et]["wins"] += 1
                    else:
                        et_stats[et]["losses"] += 1

                et_rows = [{
                    "نوع الدخول": et,
                    "فوز ✅"    : v["wins"],
                    "خسارة ❌"  : v["losses"],
                    "WR %"      : round(v["wins"] / (v["wins"] + v["losses"]) * 100)
                                  if v["wins"] + v["losses"] > 0 else 0,
                } for et, v in et_stats.items()]

                df_et = pd.DataFrame(et_rows).sort_values("WR %", ascending=False)
                st.dataframe(
                    df_et.style.background_gradient(
                        subset=["WR %"], cmap="RdYlGn", vmin=30, vmax=80,
                    ),
                    width="stretch",
                )


# ── Tab 3: Chart ───────────────────────────────────────────────────────────────
with tab3:
    st.subheader(f"رسم بياني — {selected_sym}")
    with st.spinner("جاري جلب البيانات..."):
        df_chart = fetch_chart(selected_sym)
        signal   = analyze(selected_sym, min_score=0, min_rr=0, require_mtf=False)

    if df_chart.empty:
        st.warning("لا تتوفر بيانات لهذا الأصل.")
    else:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.75, 0.25],
            vertical_spacing=0.03,
        )

        fig.add_trace(go.Candlestick(
            x=df_chart.index,
            open=df_chart["Open"], high=df_chart["High"],
            low=df_chart["Low"],   close=df_chart["Close"],
            name=selected_sym,
            increasing_line_color="#00e676",
            decreasing_line_color="#ff5252",
        ), row=1, col=1)

        sma10 = df_chart["Close"].rolling(10).mean()
        sma30 = df_chart["Close"].rolling(30).mean()
        fig.add_trace(go.Scatter(x=df_chart.index, y=sma10, name="SMA10",
                                  line=dict(color="#ffab40", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_chart.index, y=sma30, name="SMA30",
                                  line=dict(color="#40c4ff", width=1)), row=1, col=1)

        if show_levels and signal:
            last_t  = df_chart.index[-1]
            first_t = df_chart.index[max(0, len(df_chart) - 40)]
            color_d = "#00e676" if signal.direction == "call" else "#ff5252"
            for level, label, dash in [
                (signal.entry_low,  "دخول↓", "dash"),
                (signal.entry_high, "دخول↑", "dash"),
                (signal.stop,       "وقف",    "dot"),
                (signal.target1,    "هدف 1",  "longdash"),
                (signal.target2,    "هدف 2",  "longdash"),
            ]:
                lc = "#ff1744" if label == "وقف" else ("#ffd740" if "دخول" in label else color_d)
                fig.add_shape(type="line", x0=first_t, x1=last_t, y0=level, y1=level,
                              line=dict(color=lc, width=1.5, dash=dash), row=1, col=1)
                fig.add_annotation(x=last_t, y=level, text=f" {label}: {level:.2f}",
                                   showarrow=False, xanchor="left",
                                   font=dict(color=lc, size=11), row=1, col=1)

        vol_colors = ["#00e676" if c >= o else "#ff5252"
                      for c, o in zip(df_chart["Close"], df_chart["Open"])]
        fig.add_trace(go.Bar(x=df_chart.index, y=df_chart["Volume"],
                             name="Volume", marker_color=vol_colors, opacity=0.6), row=2, col=1)

        fig.update_layout(
            height=600, paper_bgcolor="#0f0f1a", plot_bgcolor="#0f0f1a",
            font_color="white", xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", y=1.02),
            margin=dict(l=10, r=10, t=10, b=10),
        )
        fig.update_xaxes(gridcolor="#1e1e2e", showgrid=True)
        fig.update_yaxes(gridcolor="#1e1e2e", showgrid=True)

        st.plotly_chart(fig, width="stretch")

        if signal:
            dir_ar    = "كول 🟢" if signal.direction == "call" else "بوت 🔴"
            conf      = "عالية 🟢" if signal.confidence == "high" else "متوسطة 🟡"
            suggested = round((signal.entry_low + signal.entry_high) / 2, 2)
            st.info(
                f"**{selected_sym}** | {dir_ar} | تقييم: **{signal.score:.1f}** | "
                f"ثقة: {conf} | R:R **{signal.rr:.1f}** | MTF: {signal.mtf_score}/2 | "
                f"دخول مقترح: **{suggested}** ◀️"
            )


# ── Tab 4: Backtest ────────────────────────────────────────────────────────────
with tab4:
    st.subheader("🧪 اختبار الاستراتيجية على بيانات تاريخية")
    st.caption("يولّد إشارات على بيانات الماضي ويتحقق من نتائجها — بدون look-ahead bias")

    bc1, bc2, bc3 = st.columns(3)
    bt_symbols   = bc1.multiselect("الأصول", current_wl, default=current_wl[:4])
    bt_days      = bc2.slider("عدد الأيام", 20, 55, 30, key="bt_days")
    bt_min_score = bc3.slider("الحد الأدنى للتقييم", 4.0, 8.0, 5.5, 0.5, key="bt_score")

    if st.button("🚀 تشغيل الـ Backtest", type="primary") and bt_symbols:
        from backtest import run_backtest as _run_bt
        with st.spinner(f"جاري التحليل على آخر {bt_days} يوم..."):
            bt_data = _run_bt(bt_symbols, bt_days, bt_min_score)

        ov = bt_data["overall"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("إجمالي الإشارات", ov["total_signals"])
        m2.metric("✅ فوز", ov["wins"])
        m3.metric("❌ خسارة", ov["losses"])
        m4.metric("🎯 معدل الفوز", f"{ov['win_rate']}%")

        st.divider()

        st.subheader("أداء كل أصل")
        rows = []
        for sym, s in bt_data["symbol_stats"].items():
            rows.append({
                "الأصل"    : sym,
                "إشارات"   : s["signals"],
                "فوز ✅"   : s["wins"],
                "خسارة ❌" : s["losses"],
                "WR %"     : s["win_rate"],
                "R:R متوسط": s["avg_rr"],
            })
        df_stats = pd.DataFrame(rows).sort_values("WR %", ascending=False)
        st.dataframe(df_stats, width="stretch")

        if bt_data["all_results"]:
            st.divider()
            st.subheader("سجل الإشارات التاريخية")
            df_bt = pd.DataFrame(bt_data["all_results"][::-1])

            def _bt_color(val):
                if "WIN"  in str(val): return "color:#00c853;font-weight:bold"
                if "LOSS" in str(val): return "color:#ff1744;font-weight:bold"
                return ""

            st.dataframe(
                df_bt.rename(columns={
                    "date":"التاريخ","symbol":"الأصل","direction":"الاتجاه",
                    "score":"التقييم","rr":"R:R","suggested_entry":"دخول مقترح",
                    "stop":"وقف","target1":"هدف1","target2":"هدف2","outcome":"النتيجة",
                }).style.map(_bt_color, subset=["النتيجة"]),
                width="stretch", height=400,
            )

            df_bt["win"] = df_bt["outcome"].apply(
                lambda x: 1 if "WIN" in str(x) else (-1 if "LOSS" in str(x) else 0))
            df_bt["cum_wins"] = (df_bt["win"] == 1).cumsum()
            df_bt["cum_loss"] = (df_bt["win"] == -1).cumsum()

            fig_bt = go.Figure()
            fig_bt.add_trace(go.Scatter(
                x=df_bt["date"], y=df_bt["cum_wins"],
                name="فوز تراكمي", line=dict(color="#00c853", width=2), fill="tozeroy",
            ))
            fig_bt.add_trace(go.Scatter(
                x=df_bt["date"], y=df_bt["cum_loss"],
                name="خسارة تراكمية", line=dict(color="#ff1744", width=2),
            ))
            fig_bt.update_layout(
                title="الأداء التراكمي عبر الزمن",
                paper_bgcolor="#0f0f1a", plot_bgcolor="#0f0f1a",
                font_color="white", height=350,
                xaxis=dict(gridcolor="#1e1e2e"),
                yaxis=dict(gridcolor="#1e1e2e"),
            )
            st.plotly_chart(fig_bt, width="stretch")
    else:
        st.info("اختر الأصول واضغط **تشغيل الـ Backtest**")


# ── Tab 5: Signal Performance (Supabase) ──────────────────────────────────────
with tab5:
    st.subheader("🏆 أداء الإشارات الحقيقية")
    st.caption("نتائج الإشارات المرسلة — يتحدث تلقائياً بعد كل صفقة")

    if not db.is_configured():
        st.warning(
            "⚠️ **Supabase غير مفعّل** — أضف `SUPABASE_URL` و `SUPABASE_KEY` في ملف `.env` "
            "لتفعيل تتبع النتائج الحقيقية."
        )
        st.stop()

    @st.cache_data(ttl=120)
    def _load_db_signals():
        return db.get_all_signals(limit=500)

    with st.spinner("جاري جلب البيانات ..."):
        raw = _load_db_signals()

    if not raw:
        st.info("📭 لا توجد إشارات مسجّلة في قاعدة البيانات بعد.")
    else:
        df_db = pd.DataFrame(raw)
        # تحويل الأنواع
        for col in ["score","rr","entry_price","stop_price","target1","target2",
                    "option_price","r_multiple"]:
            if col in df_db.columns:
                df_db[col] = pd.to_numeric(df_db[col], errors="coerce")

        # ── فئات النتائج ─────────────────────────────────────────────────────
        decided = df_db[df_db["status"].isin(["hit_t1","hit_t2","stopped"])].copy()
        open_cnt    = int((df_db["status"] == "open").sum())
        wins_t2     = int((df_db["status"] == "hit_t2").sum())
        wins_t1     = int((df_db["status"] == "hit_t1").sum())
        losses      = int((df_db["status"] == "stopped").sum())
        total_dec   = wins_t2 + wins_t1 + losses
        win_rate_db = round((wins_t2 + wins_t1) / total_dec * 100) if total_dec > 0 else 0

        # متوسط R
        avg_r  = round(float(decided["r_multiple"].mean()), 2) if not decided.empty else 0.0
        total_r = round(float(decided["r_multiple"].sum()),  2) if not decided.empty else 0.0

        # ── KPIs ─────────────────────────────────────────────────────────────
        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("📤 إجمالي الإشارات", len(df_db))
        p2.metric("🏆 Win Rate",  f"{win_rate_db}%",
                  delta=f"{wins_t1+wins_t2} فوز")
        p3.metric("❌ خسارة", losses)
        p4.metric("📂 مفتوحة", open_cnt)
        clr = "#00c853" if total_r >= 0 else "#ff1744"
        p5.metric("💹 إجمالي R",
                  f"{'+' if total_r >= 0 else ''}{total_r}R",
                  delta=f"متوسط {avg_r}R/صفقة")

        st.divider()

        # ── منحنى الأرباح التراكمية ───────────────────────────────────────────
        if not decided.empty:
            st.subheader("📈 منحنى الأرباح التراكمية")
            df_eq = decided.sort_values("outcome_time").copy()
            df_eq["cum_R"] = df_eq["r_multiple"].cumsum()
            total_clr = "#00c853" if float(df_eq["cum_R"].iloc[-1]) >= 0 else "#ff1744"

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=list(range(1, len(df_eq) + 1)),
                y=df_eq["cum_R"].tolist(),
                mode="lines+markers",
                line=dict(color=total_clr, width=2.5),
                fill="tozeroy",
                fillcolor=f"{'rgba(0,200,83,0.10)' if total_clr=='#00c853' else 'rgba(255,23,68,0.10)'}",
                text=[
                    f"{r['symbol']} {r['direction'].upper()} → "
                    f"{'✅✅ T2' if r['status']=='hit_t2' else ('✅ T1' if r['status']=='hit_t1' else '❌ Stop')}"
                    f"  ({'+' if r['r_multiple']>=0 else ''}{r['r_multiple']:.2f}R)"
                    for _, r in df_eq.iterrows()
                ],
                hoverinfo="text+y",
            ))
            fig_eq.add_hline(y=0, line_dash="dash", line_color="#555", opacity=0.8)
            fig_eq.update_layout(
                title=dict(
                    text=f"الأداء التراكمي: <b style='color:{total_clr};'>"
                         f"{'+' if total_r>=0 else ''}{total_r}R</b>"
                         f"  ({total_dec} إشارة محسومة)",
                    font=dict(color="white", size=14),
                ),
                paper_bgcolor="#0f0f1a", plot_bgcolor="#0f0f1a",
                font_color="white", height=280,
                xaxis=dict(gridcolor="#1e1e2e",
                           title=dict(text="رقم الإشارة", font=dict(color="#aaa"))),
                yaxis=dict(gridcolor="#1e1e2e",
                           title=dict(text="R Multiples", font=dict(color="#aaa"))),
                margin=dict(l=10, r=10, t=50, b=10),
                showlegend=False,
            )
            st.plotly_chart(fig_eq, use_container_width=True)
            st.divider()

        # ── Breakdown Charts ──────────────────────────────────────────────────
        if not decided.empty:
            st.subheader("🔍 تحليل جودة الإشارات")
            ba, bb = st.columns(2)

            # ── Win Rate by Score Range ────────────────────────────────────────
            with ba:
                st.caption("🎯 Win Rate حسب التقييم")
                bins   = [0, 6, 7.5, 9, 15]
                labels = ["< 6", "6 – 7.5", "7.5 – 9", "> 9"]
                decided["score_bin"] = pd.cut(
                    decided["score"], bins=bins, labels=labels, right=False
                )
                sc_grp = decided.groupby("score_bin", observed=True).agg(
                    total=("status", "count"),
                    wins =("status", lambda x: (x.isin(["hit_t1","hit_t2"])).sum()),
                ).reset_index()
                sc_grp["wr"] = (sc_grp["wins"] / sc_grp["total"] * 100).round(1)

                fig_sc = go.Figure(go.Bar(
                    x=sc_grp["score_bin"].astype(str),
                    y=sc_grp["wr"],
                    text=[f"{v:.0f}%<br>({t} إشارة)" for v, t in
                          zip(sc_grp["wr"], sc_grp["total"])],
                    textposition="outside",
                    marker_color=[
                        "#00c853" if v >= 60 else ("#ffd740" if v >= 45 else "#ff5252")
                        for v in sc_grp["wr"]
                    ],
                ))
                fig_sc.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="white", height=260, showlegend=False,
                    yaxis=dict(range=[0, 105], gridcolor="#1e1e2e"),
                    xaxis=dict(gridcolor="#1e1e2e"),
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig_sc, use_container_width=True)

            # ── Win Rate by HTF Zone ───────────────────────────────────────────
            with bb:
                st.caption("🏛️ Win Rate حسب HTF Zone")
                def _htf_label(row):
                    if not row.get("htf_zone_tf"):
                        return "بلا منطقة"
                    if row.get("cisd"):
                        return "HTF + CISD"
                    if row.get("displacement"):
                        return "HTF + Displacement"
                    return "HTF فقط"

                decided["htf_cat"] = decided.apply(_htf_label, axis=1)
                htf_grp = decided.groupby("htf_cat").agg(
                    total=("status", "count"),
                    wins =("status", lambda x: (x.isin(["hit_t1","hit_t2"])).sum()),
                ).reset_index()
                htf_grp["wr"] = (htf_grp["wins"] / htf_grp["total"] * 100).round(1)
                htf_order = ["بلا منطقة", "HTF فقط", "HTF + Displacement", "HTF + CISD"]
                htf_grp["_ord"] = htf_grp["htf_cat"].map(
                    {v: i for i, v in enumerate(htf_order)})
                htf_grp = htf_grp.sort_values("_ord")

                fig_htf = go.Figure(go.Bar(
                    x=htf_grp["htf_cat"],
                    y=htf_grp["wr"],
                    text=[f"{v:.0f}%<br>({t})" for v, t in
                          zip(htf_grp["wr"], htf_grp["total"])],
                    textposition="outside",
                    marker_color=[
                        "#00c853" if v >= 60 else ("#ffd740" if v >= 45 else "#ff5252")
                        for v in htf_grp["wr"]
                    ],
                ))
                fig_htf.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="white", height=260, showlegend=False,
                    yaxis=dict(range=[0, 105], gridcolor="#1e1e2e"),
                    xaxis=dict(gridcolor="#1e1e2e"),
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig_htf, use_container_width=True)

            # ── Win Rate by Regime + Direction ────────────────────────────────
            bc, bd = st.columns(2)

            with bc:
                st.caption("🌐 Win Rate حسب Regime")
                reg_grp = decided.groupby("regime").agg(
                    total=("status", "count"),
                    wins =("status", lambda x: (x.isin(["hit_t1","hit_t2"])).sum()),
                ).reset_index()
                reg_grp["wr"]    = (reg_grp["wins"] / reg_grp["total"] * 100).round(1)
                reg_grp["label"] = reg_grp["regime"].map(
                    {"bull": "📈 Bull", "bear": "📉 Bear", "neutral": "↔ Neutral"})

                fig_reg = go.Figure(go.Bar(
                    x=reg_grp["label"].fillna(reg_grp["regime"]),
                    y=reg_grp["wr"],
                    text=[f"{v:.0f}%<br>({t})" for v, t in
                          zip(reg_grp["wr"], reg_grp["total"])],
                    textposition="outside",
                    marker_color=[
                        "#00c853" if v >= 60 else ("#ffd740" if v >= 45 else "#ff5252")
                        for v in reg_grp["wr"]
                    ],
                ))
                fig_reg.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="white", height=240, showlegend=False,
                    yaxis=dict(range=[0, 105], gridcolor="#1e1e2e"),
                    xaxis=dict(gridcolor="#1e1e2e"),
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig_reg, use_container_width=True)

            with bd:
                st.caption("🔀 Win Rate حسب الاتجاه")
                dir_grp = decided.groupby("direction").agg(
                    total=("status", "count"),
                    wins =("status", lambda x: (x.isin(["hit_t1","hit_t2"])).sum()),
                ).reset_index()
                dir_grp["wr"]    = (dir_grp["wins"] / dir_grp["total"] * 100).round(1)
                dir_grp["label"] = dir_grp["direction"].map(
                    {"call": "CALL 🟢", "put": "PUT 🔴"})
                dir_colors = [
                    "#00e676" if d == "call" else "#ff5252"
                    for d in dir_grp["direction"]
                ]

                fig_dir = go.Figure(go.Bar(
                    x=dir_grp["label"],
                    y=dir_grp["wr"],
                    text=[f"{v:.0f}%<br>({t})" for v, t in
                          zip(dir_grp["wr"], dir_grp["total"])],
                    textposition="outside",
                    marker_color=dir_colors,
                ))
                fig_dir.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="white", height=240, showlegend=False,
                    yaxis=dict(range=[0, 105], gridcolor="#1e1e2e"),
                    xaxis=dict(gridcolor="#1e1e2e"),
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig_dir, use_container_width=True)

            # ── هيت ماب أفضل ساعات التداول ──────────────────────────────────────
            st.subheader("⏰ أفضل ساعات التداول")
            st.caption("Win Rate حسب ساعة الإرسال (توقيت ET)")

            try:
                _et_tz = pytz.timezone(config.TIMEZONE)
                df_h   = decided.copy()
                df_h["hour"] = (
                    pd.to_datetime(df_h["created_at"], utc=True)
                    .dt.tz_convert(_et_tz)
                    .dt.hour
                )
                h_grp = df_h.groupby("hour").agg(
                    total=("status", "count"),
                    wins =("status", lambda x: (x.isin(["hit_t1","hit_t2"])).sum()),
                ).reset_index()
                h_grp["wr"]    = (h_grp["wins"] / h_grp["total"] * 100).round(1)
                h_grp["label"] = h_grp["hour"].apply(
                    lambda h: f"{h:02d}:00–{h+1:02d}:00")

                fig_h = go.Figure(go.Bar(
                    x=h_grp["label"],
                    y=h_grp["wr"],
                    text=[f"{v:.0f}%<br>({t})" for v, t in
                          zip(h_grp["wr"], h_grp["total"])],
                    textposition="outside",
                    marker_color=[
                        "#00c853" if v >= 60 else ("#ffd740" if v >= 45 else "#ff5252")
                        for v in h_grp["wr"]
                    ],
                ))
                fig_h.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="white", height=260, showlegend=False,
                    yaxis=dict(range=[0, 110], gridcolor="#1e1e2e",
                               title=dict(text="Win Rate %", font=dict(color="#aaa"))),
                    xaxis=dict(gridcolor="#1e1e2e"),
                    margin=dict(l=10, r=10, t=10, b=10),
                )
                st.plotly_chart(fig_h, use_container_width=True)
            except Exception:
                st.info("بيانات غير كافية لعرض هيت ماب الساعات.")

            st.divider()

        # ── جدول الإشارات الأخيرة ──────────────────────────────────────────────
        st.subheader("📋 آخر الإشارات")

        _status_ar = {
            "open":    "🔵 مفتوحة",
            "hit_t1":  "✅ T1",
            "hit_t2":  "✅✅ T2",
            "stopped": "❌ Stop",
            "expired": "⏰ منتهية",
        }
        df_show = df_db.copy()
        df_show["النتيجة"]   = df_show["status"].map(_status_ar).fillna(df_show["status"])
        df_show["الاتجاه"]   = df_show["direction"].map({"call":"🟢 CALL","put":"🔴 PUT"})
        df_show["R مُحقَّق"] = df_show["r_multiple"].apply(
            lambda x: f"{'+' if x>=0 else ''}{x:.2f}R" if pd.notna(x) and x != 0 else "—"
        )
        df_show["الوقت"] = pd.to_datetime(df_show["created_at"]).dt.strftime("%m/%d %H:%M")

        cols_tbl = ["الوقت","symbol","الاتجاه","score","rr",
                    "entry_price","htf_zone_tf","النتيجة","R مُحقَّق"]
        cols_tbl = [c for c in cols_tbl if c in df_show.columns or c in [
            "الوقت","الاتجاه","النتيجة","R مُحقَّق"]]

        df_tbl = df_show[["الوقت","symbol","الاتجاه","score","rr",
                           "entry_price","htf_zone_tf","النتيجة","R مُحقَّق"]].rename(columns={
            "symbol":       "الأصل",
            "score":        "التقييم",
            "rr":           "R:R",
            "entry_price":  "سعر الدخول",
            "htf_zone_tf":  "HTF Zone",
        })

        def _color_result(val):
            if "T2"   in str(val): return "color:#00c853;font-weight:bold"
            if "T1"   in str(val): return "color:#64dd17;font-weight:bold"
            if "Stop" in str(val): return "color:#ff1744;font-weight:bold"
            if "مفتوحة" in str(val): return "color:#40c4ff"
            return "color:#888"

        def _color_r(val):
            if str(val).startswith("+"): return "color:#00c853;font-weight:bold"
            if str(val).startswith("-"): return "color:#ff1744;font-weight:bold"
            return ""

        st.dataframe(
            df_tbl.style
                .map(_color_result, subset=["النتيجة"])
                .map(_color_r,      subset=["R مُحقَّق"])
                .format({"التقييم": "{:.1f}", "R:R": "{:.2f}",
                         "سعر الدخول": "{:.2f}"}, na_rep="—"),
            width="stretch",
            height=450,
        )


# ── Tab 6: My Trades (دفتر الصفقات اليدوي) ─────────────────────────────────────
with tab6:
    st.subheader("💼 دفتر صفقاتي الفعلية")
    st.caption("سجّل صفقاتك الحقيقية وقارن أداءك بتوصيات البوت")

    if not db.is_configured():
        st.warning("⚠️ Supabase غير مفعّل — لا يمكن حفظ الصفقات.")
        st.stop()

    @st.cache_data(ttl=30)
    def _load_my_trades():
        return db.get_my_trades(limit=500)

    my_trades = _load_my_trades()

    # ── إحصائيات شخصية ────────────────────────────────────────────────────────
    _closed = [t for t in my_trades if t.get("status") in ("WIN", "LOSS")]
    _open   = [t for t in my_trades if t.get("status") == "OPEN"]
    if _closed:
        _w   = len([t for t in _closed if t["status"] == "WIN"])
        _l   = len(_closed) - _w
        _wr  = round(_w / len(_closed) * 100)
        _pnl = round(sum(float(t.get("pnl_dollar") or 0) for t in _closed), 2)
    else:
        _w = _l = _wr = 0
        _pnl = 0.0

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("📊 إجمالي", len(my_trades))
    m2.metric("🔵 مفتوحة", len(_open))
    m3.metric("✅ فوز", _w, delta=f"{_wr}%" if _closed else None)
    m4.metric("❌ خسارة", _l)
    _pnl_clr = "normal" if _pnl >= 0 else "inverse"
    m5.metric("💵 صافي الربح", f"{'+' if _pnl >= 0 else ''}${_pnl:.0f}")

    st.divider()

    # ── إشارة يدوية (تُرسل للتليجرام + يراقبها البوت) ──────────────────────────
    with st.expander("📡 إرسال إشارة يدوية للبوت (مراقبة تلقائية)", expanded=False):
        st.caption("شفت فرصة؟ أدخلها — تُرسل للتليجرام والبوت يراقبها: دخول → أهداف → خروج")
        sf1, sf2, sf3 = st.columns(3)
        _s_sym   = sf1.text_input("الأصل", "", key="ms_sym").upper().strip()
        _s_dir   = sf2.selectbox("الاتجاه", ["call", "put"], key="ms_dir",
                                 format_func=lambda x: "🟢 CALL" if x=="call" else "🔴 PUT")
        _s_strike= sf3.number_input("Strike", min_value=0.0, step=0.5, key="ms_strike")

        sf4, sf5, sf6 = st.columns(3)
        _s_exp   = sf4.text_input("الانتهاء (YYYYMMDD)", "", key="ms_exp").strip()
        _s_elo   = sf5.number_input("دخول من (السهم)", min_value=0.0, step=0.1, format="%.2f", key="ms_elo")
        _s_ehi   = sf6.number_input("دخول إلى (السهم)", min_value=0.0, step=0.1, format="%.2f", key="ms_ehi")

        sf7, sf8, sf9 = st.columns(3)
        _s_stop  = sf7.number_input("الوقف (السهم)", min_value=0.0, step=0.1, format="%.2f", key="ms_stop")
        _s_t1    = sf8.number_input("هدف ١ (السهم)", min_value=0.0, step=0.1, format="%.2f", key="ms_t1")
        _s_t2    = sf9.number_input("هدف ٢ (السهم)", min_value=0.0, step=0.1, format="%.2f", key="ms_t2")

        _s_opt   = st.number_input("سعر العقد التقريبي ($) — اختياري", min_value=0.0, step=0.05, format="%.2f", key="ms_opt")

        if st.button("📡 إرسال ومراقبة", type="primary", key="ms_send"):
            if _s_sym and _s_elo > 0 and _s_ehi > 0 and _s_stop > 0 and _s_t1 > 0:
                _mid = round((_s_elo + _s_ehi) / 2, 2)
                _payload = {
                    "symbol":       _s_sym,
                    "direction":    _s_dir,
                    "entry_price":  _mid,
                    "entry_low":    round(_s_elo, 2),
                    "entry_high":   round(_s_ehi, 2),
                    "stop_price":   round(_s_stop, 2),
                    "target1":      round(_s_t1, 2),
                    "target2":      round(_s_t2, 2) if _s_t2 else round(_s_t1, 2),
                    "strike":       _s_strike or None,
                    "expiry":       _s_exp or None,
                    "option_price": round(_s_opt, 2) if _s_opt else 0,
                    "entry_type":   "يدوي 📡",
                    "score":        0,
                    "rr":           round(abs(_s_t1 - _mid) / abs(_mid - _s_stop), 2) if _mid != _s_stop else 0,
                    "confidence":   "manual",
                }
                _sid = db.save_manual_signal(_payload)
                if _sid:
                    st.cache_data.clear()
                    st.success(f"✅ حُفظت إشارة {_s_sym} — البوت سيرسلها للتليجرام ويراقبها خلال لحظات")
                else:
                    st.error("❌ فشل الحفظ في Supabase")
            else:
                st.warning("أدخل: الأصل، منطقة الدخول، الوقف، وهدف ١")

    st.divider()

    # ── إضافة صفقة جديدة ──────────────────────────────────────────────────────
    with st.expander("➕ إضافة صفقة جديدة", expanded=not my_trades):
        af1, af2, af3 = st.columns(3)
        _t_sym   = af1.text_input("الأصل", "").upper().strip()
        _t_side  = af2.selectbox("الاتجاه", ["CALL", "PUT"])
        _t_entry = af3.number_input("سعر الدخول ($)", min_value=0.0, step=0.05, format="%.2f")

        af4, af5, af6 = st.columns(3)
        _t_qty    = af4.number_input("عدد العقود", min_value=1, value=1, step=1)
        _t_stop   = af5.number_input("الوقف (السهم)", min_value=0.0, step=0.1, format="%.2f")
        _t_target = af6.number_input("الهدف (السهم)", min_value=0.0, step=0.1, format="%.2f")

        _t_from = st.checkbox("من توصية البوت", value=True)
        _t_notes = st.text_input("ملاحظات", "")

        if st.button("💾 حفظ الصفقة", type="primary"):
            if _t_sym and _t_entry > 0:
                ok = db.add_my_trade({
                    "entry_date":   datetime.now().strftime("%Y-%m-%d"),
                    "symbol":       _t_sym,
                    "side":         _t_side,
                    "entry_price":  round(_t_entry, 2),
                    "contracts":    int(_t_qty),
                    "stop_price":   round(_t_stop, 2) if _t_stop else None,
                    "target_price": round(_t_target, 2) if _t_target else None,
                    "from_signal":  _t_from,
                    "status":       "OPEN",
                    "notes":        _t_notes,
                })
                if ok:
                    st.cache_data.clear()
                    st.success(f"✅ أُضيفت صفقة {_t_sym}")
                    st.rerun()
                else:
                    st.error("❌ فشل الحفظ")
            else:
                st.warning("أدخل الأصل وسعر الدخول")

    st.divider()

    # ── الصفقات المفتوحة (إغلاق) ──────────────────────────────────────────────
    if _open:
        st.subheader("🔵 الصفقات المفتوحة")
        for t in _open:
            oc1, oc2, oc3, oc4 = st.columns([3, 2, 2, 1])
            _side_emoji = "🟢" if t["side"] == "CALL" else "🔴"
            oc1.markdown(
                f"**{t['symbol']}** {_side_emoji} {t['side']}  \n"
                f"<span style='color:#888;font-size:.85rem;'>دخول: ${t['entry_price']} × {t.get('contracts',1)} عقد</span>",
                unsafe_allow_html=True,
            )
            _exit_px = oc2.number_input(
                "سعر الخروج ($)", min_value=0.0, step=0.05, format="%.2f",
                key=f"exit_{t['id']}",
            )
            if oc3.button("🔒 إغلاق", key=f"close_{t['id']}"):
                if _exit_px > 0:
                    _entry = float(t["entry_price"])
                    _qty   = int(t.get("contracts", 1))
                    # P&L أوبشنز = (خروج − دخول) × 100 × عقود
                    _pnl_d = (_exit_px - _entry) * 100 * _qty
                    _pnl_p = (_exit_px - _entry) / _entry * 100 if _entry else 0
                    _st    = "WIN" if _pnl_d >= 0 else "LOSS"
                    if db.close_my_trade(t["id"], _exit_px, _pnl_d, _pnl_p, _st):
                        st.cache_data.clear()
                        st.rerun()
            if oc4.button("🗑", key=f"del_my_{t['id']}"):
                if db.delete_my_trade(t["id"]):
                    st.cache_data.clear()
                    st.rerun()
        st.divider()

    # ── سجل الصفقات المغلقة ───────────────────────────────────────────────────
    if _closed:
        st.subheader("📋 الصفقات المغلقة")
        df_my = pd.DataFrame(_closed)
        df_my["النتيجة"] = df_my["status"].map({"WIN": "✅ فوز", "LOSS": "❌ خسارة"})
        df_my["الاتجاه"] = df_my["side"].map({"CALL": "🟢 CALL", "PUT": "🔴 PUT"})
        df_my["مصدر"]    = df_my["from_signal"].map({True: "🤖 بوت", False: "👤 شخصي"})
        df_my["P&L $"]   = df_my["pnl_dollar"].apply(
            lambda x: f"{'+' if x>=0 else ''}${x:.0f}" if pd.notna(x) else "—")
        df_my["P&L %"]   = df_my["pnl_pct"].apply(
            lambda x: f"{'+' if x>=0 else ''}{x:.1f}%" if pd.notna(x) else "—")

        df_my_show = df_my[["entry_date","symbol","الاتجاه","entry_price",
                            "exit_price","P&L $","P&L %","مصدر","النتيجة"]].rename(columns={
            "entry_date":  "التاريخ",
            "symbol":      "الأصل",
            "entry_price": "دخول",
            "exit_price":  "خروج",
        })

        def _color_pnl(val):
            if str(val).startswith("+"): return "color:#00c853;font-weight:bold"
            if str(val).startswith("-") or "خسارة" in str(val): return "color:#ff1744;font-weight:bold"
            if "فوز" in str(val): return "color:#00c853;font-weight:bold"
            return ""

        st.dataframe(
            df_my_show.style
                .map(_color_pnl, subset=["P&L $","P&L %","النتيجة"])
                .format({"دخول": "{:.2f}", "خروج": "{:.2f}"}, na_rep="—"),
            width="stretch", height=400,
        )

        # ── مقارنة البوت vs الشخصي ─────────────────────────────────────────────
        _sys = [t for t in _closed if t.get("from_signal")]
        _own = [t for t in _closed if not t.get("from_signal")]
        if _sys and _own:
            st.caption("⚖️ مقارنة: توصيات البوت مقابل اختياراتك")
            cmp1, cmp2 = st.columns(2)
            _sys_wr  = round(len([t for t in _sys if t["status"]=="WIN"])/len(_sys)*100)
            _own_wr  = round(len([t for t in _own if t["status"]=="WIN"])/len(_own)*100)
            _sys_pnl = sum(float(t.get("pnl_dollar") or 0) for t in _sys)
            _own_pnl = sum(float(t.get("pnl_dollar") or 0) for t in _own)
            cmp1.metric("🤖 صفقات البوت", f"{_sys_wr}% فوز",
                        delta=f"{'+' if _sys_pnl>=0 else ''}${_sys_pnl:.0f}")
            cmp2.metric("👤 اختياراتك", f"{_own_wr}% فوز",
                        delta=f"{'+' if _own_pnl>=0 else ''}${_own_pnl:.0f}")
    elif not _open:
        st.info("📭 لا توجد صفقات بعد — أضف أول صفقة من الأعلى.")
