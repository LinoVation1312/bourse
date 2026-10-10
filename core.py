"""Fonctions communes au bot (alertes) et au backtest : mêmes règles des deux côtés."""
import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def atr(h, l, c, n=14):
    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df):
    df = df.copy()
    df["ema20"] = ema(df.Close, 20)
    df["ema50"] = ema(df.Close, 50)
    df["rsi"] = rsi(df.Close)
    df["atr"] = atr(df.High, df.Low, df.Close)
    df["vol_rel"] = df.Volume / df.Volume.rolling(20).mean()
    df["ret63"] = df.Close.pct_change(63)
    df["ret126"] = df.Close.pct_change(126)
    return df


def tech_buy(row, bench_ret63, atr_pct_max):
    """Conditions techniques d'achat (tendance propre, pas étiré, plus fort que le Nasdaq)."""
    return bool(
        row.Close > row.ema20 > row.ema50
        and 50 < row.rsi < 70
        and row.Close / row.ema20 - 1 < 0.08
        and row.vol_rel > 0.8
        and row.ret63 > bench_ret63
        and row.atr / row.Close < atr_pct_max
    )


def stop_mult(lev):
    """Stop plus large en 1x (on laisse respirer), plus serré avec du levier."""
    return 3.0 if lev <= 1 else 2.5


def load(ticker, period="2y"):
    import yfinance as yf
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    if df.empty:
        raise ValueError(f"aucune donnée pour {ticker}")
    return df
