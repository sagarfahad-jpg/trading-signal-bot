"""
Trading Signal Bot — Dashboard
شغّله بـ:  streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json, os
from datetime import datetime
import yfinance as yf

import sys
sys.path.insert(0, os.path.dirname(__file__))
import config
from analyzer import analyze, quick_scan, get_vix, _rsi, _atr, _find_fvg, _find_order_blocks

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
    [data-testid="stMetricValue"] { font-size: 2rem; }
    .win  { color: #00c853; font-weight: 700; }
    .loss { color: #ff1744; font-weight: 700; }
    .call { color: #00e676; }
    .put  { color: #ff5252; }
    div[data-testid="stSidebarContent"] { background: #0f0f1a; }
</style>
<meta http-equiv="refresh" content="300">
""", unsafe_allow_html=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_log() -> list:
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


@st.cache_data(ttl=300)
def scan_all(min_score: float) -> pd.DataFrame:
    rows = []
    for sym in config.WATCHLIST:
        s = quick_scan(sym)
        if s:
            is_opportunity = s["score"] >= min_score and s["rr"] >= 1.5
            rows.append({
                "الأصل"  : sym,
                "الاتجاه": "🟢 CALL" if s["direction"] == "call" else "🔴 PUT",
                "التقييم": s["score"],
                "RSI"    : s["rsi"],
                "R:R"    : s["rr"],
                "السعر"  : round(s["price"], 2),
                "دخول"   : f"{s['entry_low']:.2f}–{s['entry_high']:.2f}",
                "وقف"    : s["stop"],
                "هدف 1"  : s["target1"],
                "هدف 2"  : s["target2"],
                "OB"     : "✅" if (s["bull_ob"] or s["bear_ob"]) else "—",
                "FVG"    : "✅" if (s["bull_fvg"] or s["bear_fvg"]) else "—",
                "فرصة؟"  : "✅" if is_opportunity else "❌",
            })
        else:
            rows.append({
                "الأصل": sym, "الاتجاه": "—", "التقييم": 0,
                "RSI": "—", "R:R": 0, "السعر": 0, "دخول": "—",
                "وقف": "—", "هدف 1": "—", "هدف 2": "—",
                "OB": "—", "FVG": "—", "فرصة؟": "❌",
            })
    df = pd.DataFrame(rows)
    return df.sort_values("التقييم", ascending=False).reset_index(drop=True)


@st.cache_data(ttl=60)
def fetch_chart(symbol: str):
    df = yf.Ticker(symbol).history(period="2d", interval="5m", auto_adjust=True)
    return df


def outcome_stats(log: list):
    sent = [e for e in log if e.get("sent")]
    outcomes = [e for e in sent if e.get("outcome") and "WIN" in str(e.get("outcome",""))]
    losses   = [e for e in sent if e.get("outcome") and "LOSS" in str(e.get("outcome",""))]
    total_decided = len(outcomes) + len(losses)
    win_rate = round(len(outcomes) / total_decided * 100) if total_decided > 0 else 0
    return len(sent), len(outcomes), len(losses), win_rate


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


# ─── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("⚙️ إعدادات العرض")
min_score_ui = st.sidebar.slider("حد الفرصة (Score)", 0.0, 10.0, config.MIN_SCORE, 0.5)

current_wl = load_watchlist()
selected_sym = st.sidebar.selectbox("رسم بياني للأصل", current_wl)
show_levels  = st.sidebar.checkbox("عرض مستويات الإشارة على الرسم", value=True)

st.sidebar.divider()
st.sidebar.subheader("📋 قائمة الأصول")
for sym in list(current_wl):
    c1s, c2s = st.sidebar.columns([3, 1])
    c1s.write(sym)
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
st.sidebar.caption("🔄 الصفحة تتحدث تلقائياً كل 5 دقائق")
if st.sidebar.button("🔄 تحديث الآن"):
    st.cache_data.clear()
    st.rerun()


# ─── Header ───────────────────────────────────────────────────────────────────

c1, c2 = st.columns([3, 1])
with c1:
    st.title("🤖 Trading Signal Bot")
    st.caption(f"آخر تحديث: {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}")
with c2:
    vix = get_vix()
    vix_color = "🟢" if vix < 20 else ("🟡" if vix < 28 else "🔴")
    st.metric(f"{vix_color} VIX", f"{vix:.1f}")

st.divider()

# ─── KPI Row ──────────────────────────────────────────────────────────────────

log  = load_log()
total_sent, wins, losses, win_rate = outcome_stats(log)
today_signals = [e for e in log if e.get("timestamp","").startswith(datetime.now().strftime("%Y-%m-%d"))]

k1, k2, k3, k4 = st.columns(4)
k1.metric("📤 إجمالي الإشارات", total_sent)
k2.metric("✅ فوز", wins, delta=f"{win_rate}% معدل الفوز" if total_sent else None)
k3.metric("❌ خسارة", losses)
k4.metric("📅 إشارات اليوم", len(today_signals))

st.divider()

# ─── Main content ─────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(["📊 مسح الأصول", "📋 سجل الإشارات", "📈 الرسم البياني", "🧪 Backtest"])


# ── Tab 1: Asset Scanner ───────────────────────────────────────────────────────
with tab1:
    st.subheader("مسح الأصول الحالي")
    with st.spinner("جاري تحليل الأصول..."):
        df_scan = scan_all(min_score_ui)

    # لون الصفوف حسب الفرصة
    def color_row(row):
        if row["فرصة؟"] == "✅" and "CALL" in str(row["الاتجاه"]):
            return ["background-color: #0d2b0d"] * len(row)
        elif row["فرصة؟"] == "✅" and "PUT" in str(row["الاتجاه"]):
            return ["background-color: #2b0d0d"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df_scan.style.apply(color_row, axis=1),
        width="stretch",
        height=420,
    )

    # الفرص الجاهزة فقط
    ready = df_scan[df_scan["فرصة؟"] == "✅"]
    if not ready.empty:
        st.success(f"✅ {len(ready)} فرصة تستوفي الشروط الآن")
        st.dataframe(ready[["الأصل","الاتجاه","التقييم","RSI","R:R","OB","FVG","دخول","وقف","هدف 1","هدف 2"]],
                     width="stretch")
    else:
        st.info("لا توجد فرص تستوفي الشروط حالياً")


# ── Tab 2: Signal History ──────────────────────────────────────────────────────
with tab2:
    st.subheader("سجل الإشارات المرسلة")
    if not log:
        st.info("لا توجد إشارات مسجّلة بعد.")
    else:
        df_log = pd.DataFrame(log[::-1])   # الأحدث أولاً
        cols_show = ["timestamp","symbol","direction","confidence","score","rr","vix","mtf_score",
                     "entry_low","entry_high","stop","target1","target2","sent","outcome"]
        cols_show = [c for c in cols_show if c in df_log.columns]
        df_log = df_log[cols_show].rename(columns={
            "timestamp":"الوقت","symbol":"الأصل","direction":"الاتجاه",
            "confidence":"الثقة","score":"التقييم","rr":"R:R","vix":"VIX",
            "mtf_score":"MTF","entry_low":"دخول↓","entry_high":"دخول↑",
            "stop":"وقف","target1":"هدف1","target2":"هدف2",
            "sent":"أُرسلت","outcome":"النتيجة",
        })

        def color_outcome(val):
            if val and "WIN" in str(val):   return "color: #00c853; font-weight:bold"
            if val and "LOSS" in str(val):  return "color: #ff1744; font-weight:bold"
            return ""

        st.dataframe(
            df_log.style.map(color_outcome, subset=["النتيجة"]),
            width="stretch",
            height=500,
        )

        # أداء مرئي
        if wins + losses > 0:
            st.subheader("📊 الأداء الإجمالي")
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
            c_a, c_b, c_c = st.columns([1,1,1])
            with c_b:
                st.plotly_chart(fig_pie, width="stretch")


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

        # شموع
        fig.add_trace(go.Candlestick(
            x=df_chart.index,
            open=df_chart["Open"], high=df_chart["High"],
            low=df_chart["Low"],   close=df_chart["Close"],
            name=selected_sym,
            increasing_line_color="#00e676",
            decreasing_line_color="#ff5252",
        ), row=1, col=1)

        # SMA
        sma10 = df_chart["Close"].rolling(10).mean()
        sma30 = df_chart["Close"].rolling(30).mean()
        fig.add_trace(go.Scatter(x=df_chart.index, y=sma10, name="SMA10",
                                  line=dict(color="#ffab40", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df_chart.index, y=sma30, name="SMA30",
                                  line=dict(color="#40c4ff", width=1)), row=1, col=1)

        # مستويات الإشارة
        if show_levels and signal:
            last_t = df_chart.index[-1]
            first_t = df_chart.index[max(0, len(df_chart) - 40)]
            color_d = "#00e676" if signal.direction == "call" else "#ff5252"

            for level, label, dash in [
                (signal.entry_low,  "دخول↓",  "dash"),
                (signal.entry_high, "دخول↑",  "dash"),
                (signal.stop,       "وقف",     "dot"),
                (signal.target1,    "هدف 1",   "longdash"),
                (signal.target2,    "هدف 2",   "longdash"),
            ]:
                lc = "#ff1744" if label == "وقف" else ("#ffd740" if "دخول" in label else color_d)
                fig.add_shape(type="line", x0=first_t, x1=last_t, y0=level, y1=level,
                              line=dict(color=lc, width=1.5, dash=dash), row=1, col=1)
                fig.add_annotation(x=last_t, y=level, text=f" {label}: {level:.2f}",
                                   showarrow=False, xanchor="left",
                                   font=dict(color=lc, size=11), row=1, col=1)

        # حجم التداول
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
            dir_ar = "كول 🟢" if signal.direction == "call" else "بوت 🔴"
            conf   = "عالية 🟢" if signal.confidence == "high" else "متوسطة 🟡"
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

        # نتائج كل أصل
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

        # جدول الإشارات التاريخية
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

            # رسم بياني للأداء عبر الزمن
            df_bt["win"] = df_bt["outcome"].apply(lambda x: 1 if "WIN" in x else (-1 if "LOSS" in x else 0))
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
