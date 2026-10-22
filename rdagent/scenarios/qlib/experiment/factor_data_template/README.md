# How to read files.
For example, if you want to read `filename.h5`
```Python
import pandas as pd
df = pd.read_hdf("filename.h5", key="data")
```
NOTE: **key is always "data" for all hdf5 files **.

# Here is a short description about the data

| Filename       | Description                                                      |
| -------------- | -----------------------------------------------------------------|
| "daily_pv.h5"  | Adjusted daily price and volume data.                            |


# For different data, We have some basic knowledge for them

## Daily price and volume data
$open: open price of the stock on that day.
$close: close price of the stock on that day.
$high: high price of the stock on that day.
$low: low price of the stock on that day.
$volume: volume of the stock on that day.
$factor: Qlib price-adjustment factor (复权因子) of the stock on that day. It is NOT an alpha factor and NOT a SOTA factor value; prices are already adjusted, so do not use it as a signal.

# Constraints of the execution environment
- daily_pv.h5 only contains $open, $close, $high, $low, $volume, $factor. Previously accepted (SOTA) factor values are NOT available inside factor code; recompute anything you need from these raw fields.
- Factor code is first checked on a debug sample (2018-2019, about 487 trading days, 100 stocks), then run on the full data (2008-12 onwards, about 1900 stocks). Keep look-back windows well below 480 trading days or the debug output will be empty.
- The full-data run must finish within the configured time limit (about 15 minutes). Use vectorized pandas (groupby + rolling) and avoid Python loops over dates or instruments.
- Available packages: pandas, numpy, scipy. Do not train machine learning models inside factor code.