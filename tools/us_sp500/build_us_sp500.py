"""Build a point-in-time S&P 500 daily dataset in qlib format from free sources.

Sources
- Membership: fja05680/sp500 ``sp500_ticker_start_end.csv`` (MIT), topped up with the
  current constituents table on Wikipedia for changes after its last snapshot.
- Prices: Yahoo Finance chart API (daily OHLCV + adjusted close), plus ^GSPC (price index, calendar)
  and ^SP500TR (total return, default benchmark: stock returns include dividends).
- Fallback prices: qlib's official US package (ends 2020-11-10) for tickers Yahoo no longer
  serves (mostly companies delisted after acquisitions or bankruptcies).

Known limitation: companies delisted after 2020-11 have no free price source, so they are
missing after that date. ``coverage_report.json`` quantifies this per date.

Stages (each caches its output under --work-dir):
    membership -> download -> assemble -> (dump_bin in the qlib image) -> finalize
See tools/us_sp500/README.md for the exact commands.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import requests

START = "2005-01-01"
FJA_SPANS = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
FJA_COMMITS = "https://api.github.com/repos/fja05680/sp500/commits?per_page=1"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit&includeAdjustedClose=true"
UA = {"User-Agent": "Mozilla/5.0 (research dataset build)"}
BENCHMARK = "^GSPC"  # trading calendar source (price index)
INDEXES = ("^GSPC", "^SP500TR")  # ^SP500TR (total return) is the default benchmark
OPEN_END = "2099-12-31"


def yahoo_symbol(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


# ---------------------------------------------------------------- membership
def build_membership(work: Path) -> pd.DataFrame:
    src = work / "sources"
    src.mkdir(parents=True, exist_ok=True)
    spans = pd.read_csv(io.StringIO(requests.get(FJA_SPANS, headers=UA, timeout=60).text))
    spans.to_csv(src / "sp500_ticker_start_end.csv", index=False)
    commit = requests.get(FJA_COMMITS, headers=UA, timeout=60).json()[0]
    fja_date = pd.Timestamp(commit["commit"]["committer"]["date"][:10])

    html = requests.get(WIKI, headers=UA, timeout=60).text
    (src / "wikipedia_sp500.html").write_text(html, encoding="utf-8")
    wiki = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    wiki["ticker"] = wiki["Symbol"].map(yahoo_symbol)
    wiki["added"] = pd.to_datetime(wiki["Date added"], errors="coerce")

    spans["ticker"] = spans["ticker"].map(yahoo_symbol)
    spans["start"] = pd.to_datetime(spans["start_date"])
    spans["end"] = pd.to_datetime(spans["end_date"])
    spans = spans[["ticker", "start", "end"]]

    open_fja = set(spans.loc[spans["end"].isna(), "ticker"])
    current = set(wiki["ticker"])
    new_members = wiki[~wiki["ticker"].isin(open_fja)]
    removed = sorted(open_fja - current)
    # S&P announces swaps on one effective date: removals take the earliest new-member date after the snapshot
    later_adds = new_members.loc[new_members["added"] > fja_date, "added"]
    removal_date = later_adds.min() if len(later_adds) else pd.Timestamp(date.today())
    for t in removed:
        spans.loc[(spans["ticker"] == t) & spans["end"].isna(), "end"] = removal_date
    for _, row in new_members.iterrows():
        start = row["added"] if pd.notna(row["added"]) and row["added"] > fja_date else fja_date
        spans = pd.concat([spans, pd.DataFrame([{"ticker": row["ticker"], "start": start, "end": pd.NaT}])])
    spans = spans.sort_values(["ticker", "start"]).reset_index(drop=True)
    spans = spans[spans["end"].isna() | (spans["end"] >= START)]
    spans.to_csv(work / "membership_raw.csv", index=False)
    meta = {
        "fja_last_commit": str(fja_date.date()),
        "wikipedia_current": len(current),
        "topup_added": new_members["ticker"].tolist(),
        "topup_removed": removed,
        "topup_removal_date": str(pd.Timestamp(removal_date).date()),
    }
    (work / "membership_meta.json").write_text(json.dumps(meta, indent=1))
    print(f"membership: {len(spans)} spans, {spans['ticker'].nunique()} tickers; top-up {meta}")
    return spans


# ------------------------------------------------------------------ download
def fetch_yahoo(sym: str, raw_dir: Path, retries: int = 4) -> str:
    out = raw_dir / f"{sym}.csv"
    if out.exists():
        return "cached"
    p1, p2 = int(pd.Timestamp(START).timestamp()), int(time.time())
    for attempt in range(retries):
        r = requests.get(YAHOO.format(sym=sym, p1=p1, p2=p2), headers=UA, timeout=60)
        if r.status_code == 404:
            out.with_suffix(".missing").write_text("404")
            return "404"
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        res = r.json()["chart"]["result"]
        if not res or "timestamp" not in res[0]:
            out.with_suffix(".missing").write_text("empty")
            return "empty"
        d = res[0]
        q = d["indicators"]["quote"][0]
        adj = d["indicators"].get("adjclose", [{}])[0].get("adjclose")
        df = pd.DataFrame(
            {"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"], "volume": q["volume"], "adjclose": adj},
            index=pd.to_datetime(d["timestamp"], unit="s").normalize(),
        )
        df.index.name = "date"
        df[~df.index.duplicated(keep="last")].dropna(subset=["close"]).to_csv(out)
        return "ok"
    return "error"


def download(work: Path, sleep: float) -> None:
    spans = pd.read_csv(work / "membership_raw.csv")
    raw_dir = work / "raw_yahoo"
    raw_dir.mkdir(parents=True, exist_ok=True)
    tickers = sorted(set(spans["ticker"])) + list(INDEXES)
    status = {}
    for i, sym in enumerate(tickers, 1):
        try:
            status[sym] = fetch_yahoo(sym, raw_dir)
        except Exception as e:  # noqa: BLE001 - record and continue; rerun resumes from cache
            status[sym] = f"error: {e}"[:120]
        if status[sym] != "cached":
            time.sleep(sleep)
        if i % 50 == 0:
            print(f"{i}/{len(tickers)} ...", flush=True)
    (work / "download_status.json").write_text(json.dumps(status, indent=1))
    counts = pd.Series([s if not s.startswith("error") else "error" for s in status.values()]).value_counts().to_dict()
    print("download:", counts)


# ------------------------------------------------------------------ assemble
def load_yahoo(raw_dir: Path, sym: str) -> pd.DataFrame | None:
    p = raw_dir / f"{sym}.csv"
    return pd.read_csv(p, index_col="date", parse_dates=True) if p.exists() else None


class Official:
    """Reader for qlib's official US package (prices adjusted; raw close = close / factor)."""

    def __init__(self, root: Path):
        self.root = root
        self.cal = pd.to_datetime((root / "calendars/day.txt").read_text().split())

    def field(self, sym: str, f: str) -> pd.Series | None:
        p = self.root / "features" / sym.lower() / f"{f}.day.bin"
        if not p.exists():
            return None
        a = np.fromfile(p, dtype="<f4")
        s = int(a[0])
        return pd.Series(a[1:].astype("float64"), index=self.cal[s : s + len(a) - 1])

    def frame(self, sym: str) -> pd.DataFrame | None:
        cols = {f: self.field(sym, f) for f in ("open", "high", "low", "close", "volume", "factor")}
        if cols["close"] is None:
            return None
        df = pd.DataFrame(cols).dropna(subset=["close"])
        return df if len(df) else None


def overlaps_membership(index: pd.DatetimeIndex, sym_spans: pd.DataFrame) -> bool:
    """True when a price series overlaps at least one membership span of the ticker (open spans included)."""
    lo, hi = index.min(), index.max()
    return bool(((sym_spans["start"] <= hi) & (sym_spans["end"].isna() | (sym_spans["end"] >= lo))).any())


def official_fallback(
    off: Official, inst: str, inst_spans: pd.DataFrame, cal: pd.DatetimeIndex
) -> tuple[pd.DataFrame | None, str]:
    """Official-package prices limited to the membership window, rejecting reused tickers and bad series.

    Delisted tickers in the official package often continue with a different security (or OTC junk)
    after the company left, so only [first join - 1 year, last exit] is kept.
    """
    o = off.frame(inst)
    if o is None:
        return None, ""
    lo = max(inst_spans["start"].min() - pd.Timedelta(days=365), pd.Timestamp(START))
    hi = inst_spans["end"].max() if inst_spans["end"].notna().all() else off.cal.max()
    o = o[(o.index >= lo) & (o.index <= hi)]
    days = cal[(cal >= pd.Timestamp(START)) & (cal <= off.cal.max())]
    member = np.zeros(len(days), dtype=bool)
    for _, s in inst_spans.iterrows():
        end = s["end"] if pd.notna(s["end"]) else off.cal.max() + pd.Timedelta(days=1)
        member |= (days >= s["start"]) & (days < end)
    needed = days[member]
    if len(needed) and o.index.isin(needed).sum() < 0.5 * len(needed):
        return None, f"official series covers {o.index.isin(needed).sum()}/{len(needed)} member days: rejected"
    moves = (o["close"].pct_change().abs() > 0.5).sum()
    if moves > 5:
        return None, f"official series has {moves} days with |return| > 50%: rejected"
    return (o, "") if len(o) else (None, "")


def differs_from_official(df: pd.DataFrame, off: Official, sym: str, sym_spans: pd.DataFrame) -> bool:
    """True when Yahoo's series is a different security than the official package's during membership."""
    o = off.frame(sym)
    if o is None:
        return False
    in_member = np.zeros(len(o), dtype=bool)
    for _, s in sym_spans.iterrows():
        end = s["end"] if pd.notna(s["end"]) else off.cal.max() + pd.Timedelta(days=1)
        in_member |= (o.index >= s["start"]) & (o.index < end)
    official_raw = (o["close"] / o["factor"])[in_member]
    both = pd.concat([df["close"], official_raw], axis=1, join="inner").dropna()
    if len(both) < 20:
        return False
    return not same_company(df["close"], official_raw, both.index.max() + pd.Timedelta(days=1))


def same_company(a_raw: pd.Series, b_raw: pd.Series, before: pd.Timestamp) -> bool:
    """True when two close series have the same daily returns on their overlap before ``before``.

    Returns, not price levels: sources differ in split adjustment, which only changes a few days.
    """
    return match_score(a_raw, b_raw, before) < 0.002


def match_score(a_raw: pd.Series, b_raw: pd.Series, before: pd.Timestamp, gap_days: int = 0) -> float:
    """Median absolute daily-return difference on up to 250 overlapping days ending ``gap_days`` before ``before``.

    Returns inf when the overlap is too short or fewer than 90% of days agree within 1%.
    """
    both = pd.concat([a_raw, b_raw], axis=1, join="inner").dropna()
    both = both[both.index < before - pd.Timedelta(days=gap_days)].tail(250).pct_change().dropna()
    if len(both) < 20:
        return float("inf")
    diff = (both.iloc[:, 0] - both.iloc[:, 1]).abs()
    return float(diff.median()) if (diff < 0.01).mean() > 0.9 else float("inf")


def assemble(work: Path, official_dir: Path) -> None:
    spans = pd.read_csv(work / "membership_raw.csv", parse_dates=["start", "end"])
    raw_dir = work / "raw_yahoo"
    off = Official(official_dir)
    cal = load_yahoo(raw_dir, BENCHMARK).index
    cal = cal[cal >= START]

    yahoo, source, notes = {}, {}, {}
    for sym in sorted(set(spans["ticker"])):
        df = load_yahoo(raw_dir, sym)
        sym_spans = spans[spans["ticker"] == sym]
        if df is not None and not overlaps_membership(df.index, sym_spans):
            notes[sym] = f"yahoo series {df.index.min().date()}..{df.index.max().date()} is outside every membership span: ticker reused, ignored"
            df = None
        if df is not None and differs_from_official(df, off, sym, sym_spans):
            notes[sym] = "yahoo returns disagree with the official package during membership: ticker reused, ignored"
            df = None
        if df is not None and (df["adjclose"].pct_change().abs() > 0.5).sum() > 20:
            notes[sym] = "yahoo series has more than 20 days with |return| > 50%: not the member company, ignored"
            df = None
        yahoo[sym] = df

    # renames: X ends the day Y starts and their returns match well before the switch. The comparison
    # window ends 180 days earlier because a merger target trades in lockstep with its acquirer once
    # the deal is announced. Each Y keeps only its best-matching X.
    best = {}
    ends = spans.dropna(subset=["end"])
    for _, x in ends.iterrows():
        for _, y in spans[(spans["start"] == x["end"]) & (spans["ticker"] != x["ticker"])].iterrows():
            y_df = yahoo.get(y["ticker"])
            if y_df is None:
                continue
            x_df = yahoo.get(x["ticker"])
            x_raw = x_df["close"] if x_df is not None else None
            if x_raw is None:
                o = off.frame(x["ticker"])
                x_raw = (o["close"] / o["factor"]) if o is not None else None
            if x_raw is None:
                continue
            score = match_score(x_raw, y_df["close"], x["end"], gap_days=180)
            if score < 0.002 and score < best.get(y["ticker"], (None, float("inf")))[1]:
                best[y["ticker"]] = (x["ticker"], score)
    alias = {x: y for y, (x, _) in best.items()}
    spans["instrument"] = spans["ticker"].map(lambda t: alias.get(t, t))

    csv_dir = work / "qlib_csv"
    csv_dir.mkdir(parents=True, exist_ok=True)
    for old in csv_dir.glob("*.csv"):
        old.unlink()
    big_moves = {}
    for inst in sorted(set(spans["instrument"])) + list(INDEXES):
        df = yahoo.get(inst) if inst not in INDEXES else load_yahoo(raw_dir, inst)
        if df is not None:
            f = (df["adjclose"] / df["close"]).where(lambda s: s > 0)
            out = pd.DataFrame(
                {
                    "open": df["open"] * f,
                    "high": df["high"] * f,
                    "low": df["low"] * f,
                    "close": df["adjclose"],
                    "volume": df["volume"] / f,
                    "factor": f,
                }
            )
            source[inst] = "yahoo"
        else:
            out, reason = official_fallback(off, inst, spans[spans["instrument"] == inst], cal)
            if out is None:
                source[inst] = "missing"
                if reason:
                    notes[inst] = reason
                continue
            source[inst] = "official_2020"
        out = out[out.index.isin(cal)].dropna(subset=["close"])
        out = out[(out["close"] > 0) & np.isfinite(out["close"])]
        if out.empty:
            source[inst] = "missing"
            continue
        out["change"] = out["close"].pct_change()
        big_moves[inst] = int((out["change"].abs() > 0.5).sum())
        out.index.name = "date"
        out.insert(0, "symbol", inst)
        out.to_csv(csv_dir / f"{inst}.csv")

    spans.to_csv(work / "membership.csv", index=False)
    (work / "assemble_meta.json").write_text(
        json.dumps(
            {
                "aliases": alias,
                "source_counts": pd.Series(source).value_counts().to_dict(),
                "sources": source,
                "notes": notes,
                "days_with_abs_return_over_50pct": {k: v for k, v in big_moves.items() if v},
            },
            indent=1,
        )
    )
    print("assemble:", pd.Series(source).value_counts().to_dict(), "| aliases:", alias)


# ------------------------------------------------------------------ finalize
def nyse_holidays(year: int) -> set[pd.Timestamp]:
    """NYSE full-day holidays for Nov-Dec (enough to extend the calendar to year end)."""
    nov = pd.date_range(f"{year}-11-01", f"{year}-11-30")
    thanksgiving = [d for d in nov if d.weekday() == 3][3]
    xmas = pd.Timestamp(f"{year}-12-25")
    xmas = xmas - pd.Timedelta(days=1) if xmas.weekday() == 5 else xmas + pd.Timedelta(days=1) if xmas.weekday() == 6 else xmas
    return {thanksgiving, xmas}


def finalize(work: Path, qlib_dir: Path) -> None:
    spans = pd.read_csv(work / "membership.csv", parse_dates=["start", "end"])
    days = pd.to_datetime((qlib_dir / "calendars/day.txt").read_text().split())
    last = days.max()
    future = [
        d
        for d in pd.bdate_range(last + pd.Timedelta(days=1), f"{last.year}-12-31")
        if d not in nyse_holidays(last.year)
    ]
    (qlib_dir / "calendars/day_future.txt").write_text(
        "\n".join(d.strftime("%Y-%m-%d") for d in list(days) + future) + "\n"
    )

    have = {p.name for p in (qlib_dir / "features").iterdir()}
    rows = []
    for _, s in spans.iterrows():
        if s["instrument"].lower() not in have:
            continue
        start = max(s["start"], pd.Timestamp(START)).strftime("%Y-%m-%d")
        end = OPEN_END if pd.isna(s["end"]) else (s["end"] - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        rows.append(f"{s['instrument']}\t{start}\t{end}")
    (qlib_dir / "instruments/sp500.txt").write_text("\n".join(sorted(rows)) + "\n")

    # coverage: members on each date vs members with a close on that date
    close = {}
    for inst in sorted(set(spans["instrument"])):
        p = qlib_dir / "features" / inst.lower() / "close.day.bin"
        if p.exists():
            a = np.fromfile(p, dtype="<f4")
            close[inst] = pd.Series(a[1:], index=days[int(a[0]) : int(a[0]) + len(a) - 1]).dropna().index
    report = {}
    for d in pd.date_range("2006-06-30", last, freq="6ME"):
        d = days[days <= d].max()
        members = spans[(spans["start"] <= d) & (spans["end"].isna() | (spans["end"] > d))]["instrument"].unique()
        with_data = [m for m in members if m in close and d in close[m]]
        report[str(d.date())] = {"members": len(members), "with_price": len(with_data), "coverage": round(len(with_data) / len(members), 4)}
    (qlib_dir / "coverage_report.json").write_text(json.dumps(report, indent=1))
    for k in (work / "membership_meta.json", work / "assemble_meta.json"):
        (qlib_dir / k.name).write_text(k.read_text())
    print("finalize: calendar", days.min().date(), "..", last.date(), f"+{len(future)} future days;", len(rows), "sp500 spans")
    for k, v in list(report.items())[::4] + [list(report.items())[-1]]:
        print(f"  {k}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["membership", "download", "assemble", "finalize"])
    ap.add_argument("--work-dir", type=Path, default=Path.home() / "downloads/us_sp500_build")
    ap.add_argument("--official-dir", type=Path, default=Path.home() / "downloads/qlib_us_1d_v2/extract")
    ap.add_argument("--qlib-dir", type=Path, default=Path.home() / ".qlib/qlib_data/us_data")
    ap.add_argument("--sleep", type=float, default=0.3)
    a = ap.parse_args()
    a.work_dir.mkdir(parents=True, exist_ok=True)
    if a.stage == "membership":
        build_membership(a.work_dir)
    elif a.stage == "download":
        download(a.work_dir, a.sleep)
    elif a.stage == "assemble":
        assemble(a.work_dir, a.official_dir)
    else:
        finalize(a.work_dir, a.qlib_dir)


if __name__ == "__main__":
    main()
