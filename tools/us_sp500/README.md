# US S&P 500 dataset for the qlib scenarios

`build_us_sp500.py` builds a point-in-time S&P 500 daily dataset in qlib format at
`~/.qlib/qlib_data/us_data`, used by `rdagent/scenarios/qlib/market_profiles/us_sp500.profile`.

## Sources

| Part | Source |
| --- | --- |
| Membership (2005 → today) | [fja05680/sp500](https://github.com/fja05680/sp500) `sp500_ticker_start_end.csv` (MIT), topped up with the current constituents table on Wikipedia for changes after its last snapshot |
| Prices | Yahoo Finance chart API: daily OHLCV and adjusted close from 2005-01-01 |
| Benchmark | `^GSPC` from Yahoo |
| Fallback prices | qlib's official US package (`qlib_data_us_1d_latest.zip`, ends 2020-11-10) for tickers Yahoo no longer serves |

Conventions match qlib: `$close` is the split- and dividend-adjusted close, `$factor = adjusted / raw`,
OHLC are multiplied by `$factor` and volume divided by it, `$change` is the daily return of `$close`.
Tickers use the Yahoo form (`BRK-B`). A ticker that ended membership on the day another started is
treated as a rename when their raw prices match before the switch (e.g. `FB` → `META`), so the
company keeps one instrument. A Yahoo series that starts more than 30 days after a ticker left the
index is a reused symbol and is ignored.

## Known limitations

- **Survivorship bias.** Companies delisted after acquisitions or bankruptcies are no longer on Yahoo.
  Those delisted before 2020-11 are filled from the official package; those delisted later have no
  free source. `coverage_report.json` (in the data directory) lists, every six months, how many
  index members have a price that day.
- No point-in-time fundamentals, no corporate-action history beyond what the adjusted close encodes.
- `day_future.txt` extends the NYSE calendar to year end (only Thanksgiving and Christmas are
  modelled), which is enough for backtests ending on the last data day.

## Build

```bash
cd ~/projects/RD-Agent-codex-repro
python3 -m venv ~/.venvs/usdata
~/.venvs/usdata/bin/pip install pandas==2.2.3 "numpy<2.1" lxml==5.3.0 requests==2.32.3 pyarrow==17.0.0 beautifulsoup4==4.12.3 html5lib==1.1
B=~/.venvs/usdata/bin/python
$B tools/us_sp500/build_us_sp500.py membership
$B tools/us_sp500/build_us_sp500.py download      # ~15 min; rerun resumes from the cache
$B tools/us_sp500/build_us_sp500.py assemble      # needs the official package extracted at ~/downloads/qlib_us_1d_v2/extract
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v ~/downloads/us_sp500_build:/work -v ~/.qlib/qlib_data:/out local_qlib:latest \
  python /workspace/qlib/scripts/dump_bin.py dump_all --data_path /work/qlib_csv --qlib_dir /out/us_data \
  --include_fields open,close,high,low,volume,factor,change --date_field_name date --symbol_field_name symbol
$B tools/us_sp500/build_us_sp500.py finalize      # sp500.txt, day_future.txt, coverage_report.json
```

To refresh with newer prices, delete `~/downloads/us_sp500_build/raw_yahoo` and `~/.qlib/qlib_data/us_data`,
then rerun all stages. Move `git_ignore_folder/factor_implementation_source_data*_us` aside afterwards so the
factor-coding data is regenerated.
