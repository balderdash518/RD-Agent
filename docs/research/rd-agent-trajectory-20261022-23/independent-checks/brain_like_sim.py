"""BRAIN-style long-short simulation of the 10 SOTA factors on the local US data, to compare with WorldQuant BRAIN.

weights: alpha -> market-neutral (demean within universe) -> sum|w| = 1, rebalanced daily.
Delay 1: weights from data through day T earn close(T+2)/close(T+1)-1 (same as RD-Agent label).
Returns = 2 * 252 * mean daily PnL / booksize (BRAIN: annual PnL over half book); Turnover = sum|dw| per day;
Fitness = Sharpe * sqrt(|Returns| / max(Turnover, 0.125)).
Usage (WSL): python -I brain_like_sim.py <trials.json> [universe: sp500|all]"""
import json
import os
import sys

import numpy as np
import pandas as pd

trials = json.load(open(sys.argv[1], encoding="utf-8"))
UNIV = sys.argv[2] if len(sys.argv) > 2 else "sp500"
SRC = "/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/factor_implementation_source_data_us/daily_pv.h5"
close = pd.read_hdf(SRC, key="data")["$close"].unstack("instrument").sort_index()
close = close[[c for c in close.columns if not c.startswith("^")]]
ret = close.pct_change(fill_method=None)
fwd = ret.shift(-2)  # delay 1

if UNIV == "sp500":
    spans = pd.read_csv(os.path.expanduser("~/.qlib/qlib_data/us_data/instruments/sp500.txt"), sep="\t", header=None, names=["i", "s", "e"], parse_dates=["s", "e"])
    member = pd.DataFrame(False, index=close.index, columns=close.columns)
    for r in spans.itertuples():
        if r.i in member.columns:
            member.loc[(member.index >= r.s) & (member.index <= r.e), r.i] = True
else:
    member = close.notna()


def stats(w, f):
    pnl = (w * f).sum(axis=1)
    tov = w.diff().abs().sum(axis=1)
    out = {}
    for name, (a, b) in {"2019": ("2019", "2019"), "2020": ("2020", "2020"), "2021": ("2021", "2021"), "2022": ("2022", "2022"),
                         "2023": ("2023", "2023"), "IS 2019-23": ("2019", "2023"), "2024-26": ("2024", "2026")}.items():
        p, t = pnl.loc[a:b], tov.loc[a:b]
        sharpe = p.mean() / p.std() * np.sqrt(252)
        rets = 2 * 252 * p.mean()
        to = t.mean()
        out[name] = (float(round(sharpe, 2)), float(round(rets * 100, 2)), float(round(to * 100, 1)), float(round(sharpe * np.sqrt(abs(rets) / max(to, 0.125)), 2)))
    return out, pnl


print("universe", UNIV, "| cells: (Sharpe, Returns%, Turnover%, Fitness)")
pnls = {}
for t in trials:
    if not t["feedback"]["decision"]:
        continue
    for task, ws in zip(t["tasks"], t["factor_workspaces"]):
        s = pd.read_hdf(ws["path"] + "/result.h5", key="data").iloc[:, 0]
        if list(s.index.names) != ["datetime", "instrument"]:
            s.index = s.index.set_names(["datetime", "instrument"])
        a = s.unstack("instrument").reindex(index=close.index, columns=close.columns)
        a = a.where(member & close.notna())
        a = a.rank(axis=1, pct=True)  # BRAIN-style rank inside the universe
        w = a.sub(a.mean(axis=1), axis=0)
        w = w.div(w.abs().sum(axis=1), axis=0).fillna(0.0)
        st, pnl = stats(w, fwd.fillna(0.0))
        pnls[f"F{len(pnls) + 1}"] = pnl.loc["2019":"2023"] * np.sign(st["IS 2019-23"][0])
        print(f"L{t['loop']:<2} {task['factor_name'][:48]:48s}", " ".join(f"{k}:{v}" for k, v in st.items()), flush=True)
# PnL correlation of the sign-aligned (IS Sharpe > 0) versions, 2019-2023
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
print(pd.DataFrame(pnls).corr().round(2))
