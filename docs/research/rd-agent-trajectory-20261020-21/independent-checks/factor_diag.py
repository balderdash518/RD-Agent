"""Independent single-factor diagnostics for the 2026-10-20 run (csi500 universe, qlib label).

Usage: python -I factor_diag.py <dir containing factors.json>  ->  writes <dir>/factor_diag.json."""
import json
import os
import sys

import numpy as np
import pandas as pd

S = sys.argv[1]
SRC = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/factor_implementation_source_data/daily_pv.h5"
INST = os.path.expanduser("~/.qlib/qlib_data/cn_data/instruments/csi500.txt")

pv = pd.read_hdf(SRC, key="data").sort_index()
pv = pv[pv.index.get_level_values("datetime") >= "2009-01-01"]

# csi300 membership mask
spans = pd.read_csv(INST, sep="\t", header=None, names=["inst", "s", "e"])
pv = pv[pv.index.get_level_values("instrument").isin(set(spans.inst))]
idx = pv.index
dt = idx.get_level_values("datetime")
ins = idx.get_level_values("instrument")
mask = np.zeros(len(idx), dtype=bool)
pos = pd.Series(np.arange(len(idx)), index=idx)
for r in spans.itertuples():
    try:
        sub = pos.xs(r.inst, level="instrument")
    except KeyError:
        continue
    sub = sub[(sub.index >= r.s) & (sub.index <= r.e)]
    mask[sub.values] = True
keep = pd.Series(mask, index=idx)

g = lambda s: s.groupby(level="instrument")
c, o, h, l, v = pv["$close"], pv["$open"], pv["$high"], pv["$low"], pv["$volume"]
label = g(c).shift(-2) / g(c).shift(-1) - 1

# Alpha20 base features reimplemented in pandas
def roll(s, n, f):
    return g(s).rolling(n, min_periods=n).agg(f).droplevel(0).reindex(s.index)

def rcorr(a, b, n):
    ma, mb = roll(a, n, "mean"), roll(b, n, "mean")
    cov = roll(a * b, n, "mean") - ma * mb
    va = roll(a * a, n, "mean") - ma * ma
    vb = roll(b * b, n, "mean") - mb * mb
    return (cov / np.sqrt(va * vb)).replace([np.inf, -np.inf], np.nan)

tt = g(c).cumcount().astype(float)
base = {}
ret1 = c / g(c).shift(1) - 1
lv = np.log(v + 1)
for n in (5, 10, 20, 60):
    if n in (5, 10, 20, 60):
        base[f"RSQR{n}"] = rcorr(c, tt, n) ** 2
        base[f"CORR{n}"] = rcorr(c, lv, n)
for n in (5, 10, 60):
    base[f"CORD{n}"] = rcorr(c / g(c).shift(1), np.log(v / g(v).shift(1) + 1), n)
for n in (5, 10):
    my, mt = roll(c, n, "mean"), roll(tt, n, "mean")
    cov = roll(c * tt, n, "mean") - my * mt
    var = roll(tt * tt, n, "mean") - mt * mt
    b = cov / var
    base[f"RESI{n}"] = (c - (my + b * (tt - mt))) / c
aw = (ret1.abs() * v)
for n in (5, 60):
    base[f"WVMA{n}"] = roll(aw, n, "std") / (roll(aw, n, "mean") + 1e-12)
base["KLEN"] = (h - l) / o
base["KLOW"] = (np.minimum(o, c) - l) / o
base["ROC60"] = g(c).shift(60) / c
base["VSTD5"] = roll(v, 5, "std") / (v + 1e-12)
base["STD5"] = roll(c, 5, "std") / c
print("base features", len(base), flush=True)

periods = {"train": ("2010-01-01", "2017-12-31"), "valid": ("2018-01-01", "2019-12-31"), "test": ("2020-01-01", "2022-12-31")}
sel = keep & (dt >= "2010-01-01") & (dt <= "2022-12-31")
sel = sel.values
lab = label[sel]
labr = lab.groupby(level="datetime").rank(pct=True)


def xrank(s):
    s = s.reindex(idx)[sel]
    return s.groupby(level="datetime").rank(pct=True)


def daily_corr(a, b):
    df = pd.DataFrame({"a": a, "b": b}).dropna()
    df["ab"], df["aa"], df["bb"] = df.a * df.b, df.a * df.a, df.b * df.b
    m = df.groupby(level="datetime").mean()
    n = df.groupby(level="datetime").size()
    r = (m.ab - m.a * m.b) / np.sqrt((m.aa - m.a**2) * (m.bb - m.b**2))
    return r[n >= 50]


base_r = {k: xrank(s) for k, s in base.items()}

facs = json.load(open(f"{S}/factors.json"))
ranks = {}
rows = []
for f in facs:
    df = pd.read_hdf(f"{f['path']}/result.h5")
    s = df.iloc[:, 0]
    s.index = s.index.reorder_levels(["datetime", "instrument"]) if s.index.names[0] != "datetime" else s.index
    s = s[~s.index.duplicated()]
    r = xrank(s)
    key = f"L{f['loop']}:{f['name']}"
    ranks[key] = r.astype("float32")
    ic = daily_corr(r, labr)
    row = {"key": key, "loop": f["loop"], "coverage": float(r.notna().mean())}
    for p, (a, b) in periods.items():
        x = ic[(ic.index >= a) & (ic.index <= b)]
        row[f"ric_{p}"] = float(x.mean())
        row[f"icir_{p}"] = float(x.mean() / x.std())
    bc = {k: float(daily_corr(r, br).mean()) for k, br in base_r.items()}
    kb = max(bc, key=lambda k: abs(bc[k]))
    row["max_base"], row["max_base_corr"] = kb, bc[kb]
    rows.append(row)
    print(key, {k: round(v, 4) if isinstance(v, float) else v for k, v in row.items() if k != "key"}, flush=True)

# base feature own ICs
brows = []
for k, br in base_r.items():
    ic = daily_corr(br, labr)
    brows.append({"key": k, **{f"ric_{p}": float(ic[(ic.index >= a) & (ic.index <= b)].mean()) for p, (a, b) in periods.items()}})

# pairwise mean cross-sectional corr among new factors (test period sampled every 5th day)
keys = list(ranks)
days = sorted(set(labr.index.get_level_values("datetime")))
samp = set(days[::5])
M = pd.DataFrame({k: v[v.index.get_level_values("datetime").isin(samp)] for k, v in ranks.items()})
cm = {}
for d0, grp in M.groupby(level="datetime"):
    cm[d0] = grp.corr(min_periods=50)
pair = sum(cm.values()) / len(cm)
json.dump({"factors": rows, "base": brows, "pair": pair.round(4).to_dict()}, open(f"{S}/factor_diag.json", "w"))
print("done")
