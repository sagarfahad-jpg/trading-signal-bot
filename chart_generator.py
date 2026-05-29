"""
Chart Generator — يولّد صورة رسم بياني مع مستويات الإشارة
يُستخدم لإرسال الصورة مع إشارة Telegram
"""

import io
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import pandas as pd
import numpy as np
from analyzer import SignalResult


def generate_signal_chart(df: pd.DataFrame, signal: SignalResult) -> bytes:
    """
    يولّد صورة PNG للرسم البياني مع مستويات الإشارة.
    يُرجع bytes جاهزة للإرسال عبر Telegram.
    """
    try:
        df = df.tail(80).copy()
        if df.empty:
            return b""

        fig, (ax1, ax2) = plt.subplots(
            2, 1, figsize=(12, 7),
            gridspec_kw={'height_ratios': [3, 1]},
            facecolor='#0f0f1a',
        )
        fig.subplots_adjust(hspace=0.05)

        # ── ألوان ─────────────────────────────────────────────────────────────
        is_call   = signal.direction == 'call'
        dir_color = '#00e676' if is_call else '#ff5252'

        # ── الشموع اليابانية ──────────────────────────────────────────────────
        ax1.set_facecolor('#0f0f1a')
        x = range(len(df))
        for i, (_, row) in enumerate(df.iterrows()):
            o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']
            color = '#00e676' if c >= o else '#ff5252'
            ax1.plot([i, i], [l, h], color=color, linewidth=0.8, alpha=0.9)
            ax1.bar(i, abs(c - o), bottom=min(o, c), color=color,
                    width=0.6, alpha=0.9, linewidth=0)

        # ── SMA ───────────────────────────────────────────────────────────────
        sma10 = df['Close'].rolling(10).mean()
        sma30 = df['Close'].rolling(30).mean()
        ax1.plot(x, sma10, color='#ffab40', linewidth=1.0, alpha=0.8, label='SMA10')
        ax1.plot(x, sma30, color='#40c4ff', linewidth=1.0, alpha=0.8, label='SMA30')

        # ── VWAP ──────────────────────────────────────────────────────────────
        if signal.vwap > 0:
            ax1.axhline(signal.vwap, color='#ce93d8', linewidth=1.0,
                        linestyle='--', alpha=0.7, label=f'VWAP {signal.vwap:.2f}')

        # ── مستويات الإشارة ───────────────────────────────────────────────────
        levels = [
            (signal.entry_low,  'دخول↓', '#ffd740', '--'),
            (signal.entry_high, 'دخول↑', '#ffd740', '--'),
            (signal.stop,       'وقف',   '#ff1744', ':'),
            (signal.target1,    'هدف ١', dir_color, '-'),
            (signal.target2,    'هدف ٢', dir_color, '-'),
        ]
        for level, label, color, ls in levels:
            ax1.axhline(level, color=color, linewidth=1.2,
                        linestyle=ls, alpha=0.85)
            ax1.text(len(df) - 1, level, f' {label}: {level:.2f}',
                     color=color, fontsize=8, va='center',
                     fontfamily='DejaVu Sans')

        # ── Zone ──────────────────────────────────────────────────────────────
        ax1.axhspan(signal.entry_low, signal.entry_high,
                    alpha=0.08, color='#ffd740')

        # ── عنوان ─────────────────────────────────────────────────────────────
        dir_ar    = 'CALL 🟢' if is_call else 'PUT 🔴'
        regime_ar = {'bull': '📈 Bull', 'bear': '📉 Bear', 'neutral': '↔ Neutral'}.get(signal.regime, '')
        ax1.set_title(
            f'{signal.symbol}  |  {dir_ar}  |  Score: {signal.score:.1f}★  |  '
            f'R:R {signal.rr:.2f}  |  {signal.entry_type}  |  {regime_ar}',
            color='white', fontsize=11, pad=8,
        )
        ax1.tick_params(colors='#888', labelbottom=False)
        ax1.spines[:].set_color('#2a2a40')
        ax1.yaxis.set_label_position('right')
        ax1.yaxis.tick_right()
        ax1.grid(color='#1e1e2e', linewidth=0.5)
        ax1.legend(fontsize=8, facecolor='#0f0f1a', labelcolor='white',
                   edgecolor='#2a2a40', loc='upper left')

        # ── Volume ────────────────────────────────────────────────────────────
        ax2.set_facecolor('#0f0f1a')
        vol_colors = ['#00e676' if c >= o else '#ff5252'
                      for c, o in zip(df['Close'], df['Open'])]
        ax2.bar(x, df['Volume'], color=vol_colors, alpha=0.7, width=0.6)
        ax2.tick_params(colors='#888')
        ax2.spines[:].set_color('#2a2a40')
        ax2.grid(color='#1e1e2e', linewidth=0.5)
        ax2.set_ylabel('Vol', color='#888', fontsize=8)

        # ── Export ────────────────────────────────────────────────────────────
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=130,
                    bbox_inches='tight', facecolor='#0f0f1a')
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        print(f"  [chart] {e}")
        return b""
