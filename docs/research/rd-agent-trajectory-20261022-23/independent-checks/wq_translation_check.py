"""Re-implement each WorldQuant BRAIN expression for the 10 SOTA factors with pandas (same algebra as the
Fast Expression, BRAIN-like operators) and compare with the exact factor values RD-Agent produced.

Usage (WSL): python -I wq_translation_check.py  ->  mean daily cross-sectional Spearman corr, 2020-2022."""
import numpy as np
import pandas as pd

WS = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/RD-Agent_workspace/"
SRC = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/factor_implementation_source_data_us/daily_pv.h5"
pv = pd.read_hdf(SRC, key="data").sort_index()
close, open_, high, low, volume = (pv[k].unstack("instrument") for k in ["$close", "$open", "$high", "$low", "$volume"])
returns = close.pct_change(fill_method=None)

# BRAIN-like operators on wide (date x instrument) frames
rank = lambda x: x.rank(axis=1, pct=True)
ts_delay = lambda x, d: x.shift(d)
ts_mean = lambda x, d: x.rolling(d).mean()
ts_sum = lambda x, d: x.rolling(d).sum()
ts_max = lambda x, d: x.rolling(d).max()
ts_std_dev = lambda x, d: x.rolling(d).std()
ts_zscore = lambda x, d: (x - ts_mean(x, d)) / ts_std_dev(x, d)
ts_corr = lambda x, y, d: x.rolling(d).corr(y)
emax = lambda *xs: pd.concat([x.stack() for x in xs], axis=1).max(axis=1).unstack()
group_median_market = lambda x: pd.DataFrame(np.repeat(x.median(axis=1).values[:, None], x.shape[1], 1), index=x.index, columns=x.columns)


def ts_regression(y, x, d, rettype):
    """rettype 1 = intercept, 2 = slope, 4 = SSE (BRAIN numbering, to be confirmed in BRAIN docs)."""
    mx, my = ts_mean(x, d), ts_mean(y, d)
    sxx = ts_sum(x * x, d) - d * mx * mx
    sxy = ts_sum(x * y, d) - d * mx * my
    syy = ts_sum(y * y, d) - d * my * my
    b = sxy / sxx
    return {1: my - b * mx, 2: b, 4: syy - b * sxy}[rettype]


def det3(a, b, c, d, e, f, g, h, i):
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def loop32():
    x1 = returns
    x2 = volume / ts_delay(volume, 1) - 1
    x3 = ts_std_dev(returns, 5)
    loc = ((close - low) / (high - low)).where(high > low, 0.5)
    x4 = ts_mean(loc, 20)
    r12, r13, r14 = ts_corr(x1, x2, 20), ts_corr(x1, x3, 20), ts_corr(x1, x4, 20)
    r23, r24, r34 = ts_corr(x2, x3, 20), ts_corr(x2, x4, 20), ts_corr(x3, x4, 20)
    d = 1 + 0.1 / 19
    w1 = det3(d, r23, r24, r23, d, r34, r24, r34, d)
    w2 = -det3(r12, r13, r14, r23, d, r34, r24, r34, d)
    w3 = det3(r12, r13, r14, d, r23, r24, r24, r34, d)
    w4 = -det3(r12, r13, r14, d, r23, r24, r23, d, r34)
    num = w1 * ts_zscore(x1, 20) + w2 * ts_zscore(x2, 20) + w3 * ts_zscore(x3, 20) + w4 * ts_zscore(x4, 20)
    q = w1**2 + w2**2 + w3**2 + w4**2 + 2 * (r12 * w1 * w2 + r13 * w1 * w3 + r14 * w1 * w4 + r23 * w2 * w3 + r24 * w2 * w4 + r34 * w3 * w4)
    return ts_mean(rank(num / np.sqrt(q)), 3)


def loop15():
    m = group_median_market(returns)
    a, b = ts_regression(returns, m, 20, 1), ts_regression(returns, m, 20, 2)
    sse = ts_regression(returns, m, 20, 4)
    return rank((ts_sum(returns, 10) - 10 * a - b * ts_sum(m, 10)) / np.sqrt(sse / 19))


tr = emax(high - low, (high - ts_delay(close, 1)).abs(), (low - ts_delay(close, 1)).abs())
WQ = {
    ("144cdaf522c64ac8975c33f31eef0f84", "Loop2 AmihudDollarVolume"): lambda: rank(ts_mean(returns.abs() / (close * volume), 20)),
    (None, "Loop2 MA5ATRNormCloseToHigh"): lambda: ts_mean((close / ts_max(high, 10) - 1) / ts_mean(tr, 20), 5),
    (None, "Loop2 MA5OvernightGap"): lambda: ts_mean(open_ / ts_delay(close, 1) - 1, 5),
    (None, "Loop7 ShortTermReversal10D"): lambda: rank(-(close / ts_delay(close, 10) - 1)),
    (None, "Loop7 MA5IntradayReturn"): lambda: rank(ts_mean(close / open_ - 1, 5)),
    (None, "Loop10 CloseLocation5D"): lambda: rank(ts_mean((close - low) / (high - low), 5)),
    (None, "Loop10 VolumeWeightedReturn5D"): lambda: rank(ts_sum(volume * returns, 5) / ts_sum(volume, 5)),
    (None, "Loop10 DownsideDeviation20D"): lambda: rank(ts_std_dev(returns.clip(upper=0), 20)),
    (None, "Loop15 VolScaledResidualMomentum10D"): loop15,
    (None, "Loop32 RidgeResidualVolScaled3D"): loop32,
}
import json, sys
trials = json.load(open(sys.argv[1], encoding="utf-8"))
paths = [ws["path"] for t in trials if t["feedback"]["decision"] for ws in t["factor_workspaces"]]
for path, ((_, name), fn) in zip(paths, WQ.items()):
    exact = pd.read_hdf(path + "/result.h5", key="data").iloc[:, 0]
    if list(exact.index.names) != ["datetime", "instrument"]:
        exact.index = exact.index.set_names(["datetime", "instrument"])
    exact = exact.unstack("instrument").loc["2020":"2022"]
    mine = fn().reindex_like(exact)
    c = exact.rank(axis=1).corrwith(mine.rank(axis=1), axis=1).mean()
    print(f"{name:40s} spearman={c:.4f}", flush=True)
