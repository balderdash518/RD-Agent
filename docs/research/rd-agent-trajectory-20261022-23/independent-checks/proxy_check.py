"""Check simple WorldQuant-expressible proxies against the exact SOTA factor values (US run 2026-10-22).

Usage (WSL): python -I proxy_check.py  ->  prints mean daily cross-sectional Spearman corr, 2020-2022."""
import numpy as np
import pandas as pd

WS = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/RD-Agent_workspace/"
SRC = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/factor_implementation_source_data_us/daily_pv.h5"
pv = pd.read_hdf(SRC, key="data").sort_index()
c, o, h, l, v = (pv[k].unstack("instrument") for k in ["$close", "$open", "$high", "$low", "$volume"])
ret = c.pct_change(fill_method=None)


def exact(ws):
    s = pd.read_hdf(WS + ws + "/result.h5", key="data").iloc[:, 0]
    return s.unstack("instrument")


def corr(a, b):
    a, b = a.loc["2020":"2022"], b.reindex_like(a)
    ra, rb = a.rank(axis=1), b.rank(axis=1)
    return ra.corrwith(rb, axis=1).mean()


z20 = (ret - ret.rolling(20).mean()) / ret.rolling(20).std()
proxies = {
    "ts_mean(rank(ts_zscore(returns,20)),3)": z20.rank(axis=1, pct=True).rolling(3).mean(),
    "ts_mean(rank(returns),3)": ret.rank(axis=1, pct=True).rolling(3).mean(),
}
e32 = exact("4dae81fd11094201a79408b60cce927c")
for name, p in proxies.items():
    print("Loop32 vs", name, round(corr(e32, p), 4))

# Loop 2 MA5ATRNormalizedCloseToHigh10D: price-level dependence of dividing a ratio by ATR (in $)
tr = pd.concat([(h - l).stack(), (h - c.shift()).abs().stack(), (l - c.shift()).abs().stack()], axis=1).max(axis=1).unstack()
raw = ((c / h.rolling(10).max() - 1) / tr.rolling(20).mean()).rolling(5).mean()
fixed = ((c / h.rolling(10).max() - 1) / (tr.rolling(20).mean() / c)).rolling(5).mean()
print("Loop2 ATR factor: corr(raw, 1/close)", round(corr(raw.abs(), 1 / c), 4), " corr(raw, unit-free version)", round(corr(raw, fixed), 4))
