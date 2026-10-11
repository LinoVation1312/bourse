"""Indicateurs techniques (lissage de Wilder pour RSI et ATR)."""
import pandas as pd


def rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn.where(dn != 0))
    return out.where(dn != 0, 100.0).where(close.diff().notna())


def atr(h, l, c, n=14):
    pc = c.shift()
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def compute(df):
    """Retourne OHLCV + indicateurs. L'index doit être trié et sans doublon."""
    d = df.copy()
    c = d.Close.astype(float)
    d["sma5"] = c.rolling(5).mean()
    d["sma20"] = c.rolling(20).mean()
    d["sma50"] = c.rolling(50).mean()
    d["sma200"] = c.rolling(200).mean()
    d["rsi2"] = rsi(c, 2)
    d["rsi14"] = rsi(c, 14)
    d["atr"] = atr(d.High.astype(float), d.Low.astype(float), c)
    d["atr_pct"] = d.atr / c
    v = d.Volume.astype(float) if "Volume" in d else pd.Series(float("nan"), index=d.index)
    d["vol_rel"] = v / v.rolling(20).mean()
    d["hi20"] = c.rolling(20).max()
    d["sig20"] = c.pct_change().rolling(20).std()
    d["ret5"] = c.pct_change(5)
    d["ret20"] = c.pct_change(20)
    d["vol_ann"] = c.pct_change().rolling(60).std() * (252 ** 0.5)
    return d
