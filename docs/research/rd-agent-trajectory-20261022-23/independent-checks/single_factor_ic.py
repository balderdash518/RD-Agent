"""Single-factor Rank IC for every factor of run 2026-10-22_16-11-01 (US S&P 500, qlib label), independent of LightGBM.

Usage (WSL): python -I single_factor_ic.py <trials.json> <out.json>"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

trials = json.load(open(sys.argv[1], encoding="utf-8"))
SRC = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/factor_implementation_source_data_us/daily_pv.h5"
INST = os.path.expanduser("~/.qlib/qlib_data/us_data/instruments/sp500.txt")

pv = pd.read_hdf(SRC, key="data").sort_index()
spans = pd.read_csv(INST, sep="\t", header=None, names=["inst", "s", "e"], parse_dates=["s", "e"])
c = pv["$close"]
label = c.groupby(level="instrument").shift(-2) / c.groupby(level="instrument").shift(-1) - 1
idx = label.index
m = pd.merge(idx.to_frame(index=False), spans, left_on="instrument", right_on="inst", how="left")
member = ((m["datetime"] >= m["s"]) & (m["datetime"] <= m["e"])).groupby([m["datetime"], m["instrument"]]).any()
label = label[member.reindex(idx, fill_value=False).values]
PERIODS = {"valid_2018_19": ("2018-01-01", "2019-12-31"), "select_2020_22": ("2020-01-01", "2022-12-31")}


def rank_ic(f):
    df = pd.concat([f.rename("f"), label.rename("y")], axis=1, join="inner").dropna()
    out = {}
    for name, (a, b) in PERIODS.items():
        d = df[(df.index.get_level_values("datetime") >= a) & (df.index.get_level_values("datetime") <= b)]
        r = d.groupby(level="datetime").rank(pct=True)
        daily = r.groupby(level="datetime").apply(lambda x: x["f"].corr(x["y"]) if len(x) > 20 else np.nan).dropna()
        out[name] = {"rank_ic": float(daily.mean()), "t": float(daily.mean() / daily.std() * np.sqrt(len(daily))), "days": int(len(daily))}
    return out


def load_factor(ws):
    p = Path(ws) / "result.h5"
    if not p.exists():
        return None
    s = pd.read_hdf(p, key="data")
    s = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
    if list(s.index.names) != ["datetime", "instrument"]:
        # Some factor.py files drop level names; identify levels by dtype instead.
        dt_first = pd.api.types.is_datetime64_any_dtype(s.index.get_level_values(0))
        s.index = s.index.set_names(["datetime", "instrument"] if dt_first else ["instrument", "datetime"])
        if not dt_first:
            s = s.swaplevel()
        s = s.sort_index()
    return s.astype(float)


rows, cache = [], {}
for t in trials:
    for task, ws in zip(t["tasks"], t["factor_workspaces"]):
        name = task["factor_name"]
        f = load_factor(ws["path"])
        if f is None:
            rows.append({"loop": t["loop"], "factor": name, "missing": True})
            continue
        cache[(t["loop"], name)] = f
        rows.append({"loop": t["loop"], "factor": name, "accepted": t["feedback"]["decision"], **rank_ic(f)})
        print(json.dumps(rows[-1]), flush=True)

# Max |daily rank correlation| of each new factor with factors already in SOTA at that loop (select period).
sota = []
for t in trials:
    for row in [r for r in rows if r["loop"] == t["loop"] and not r.get("missing")]:
        f = cache[(t["loop"], row["factor"])]
        f = f[f.index.get_level_values("datetime") >= "2020-01-01"]
        best = (0.0, None)
        for (sl, sn) in sota:
            g = cache[(sl, sn)]
            d = pd.concat([f.rename("a"), g.rename("b")], axis=1, join="inner").dropna()
            rk = d.groupby(level="datetime").rank()
            corr = rk.groupby(level="datetime").apply(lambda x: x["a"].corr(x["b"])).mean()
            if abs(corr) > abs(best[0]):
                best = (float(corr), sn)
        row["max_corr_with_sota"] = best
    if t["feedback"]["decision"]:
        sota += [(t["loop"], task["factor_name"]) for task in t["tasks"] if (t["loop"], task["factor_name"]) in cache]
json.dump(rows, open(sys.argv[2], "w"), indent=1)
