import os

import qlib

qlib.init(
    provider_uri=os.environ.get("QLIB_PROVIDER_URI", "~/.qlib/qlib_data/cn_data"),
    region=os.environ.get("QLIB_REGION", "cn"),
)

from qlib.data import D

# Stock universe of the factor source data, e.g. "all" or "csi800" (= csi300 + csi500 members).
instruments = D.instruments(os.environ.get("FACTOR_SOURCE_INSTRUMENTS", "all"))
fields = ["$open", "$close", "$high", "$low", "$volume", "$factor"]
data = D.features(instruments, fields, freq="day").swaplevel().sort_index().loc["2008-12-29":].sort_index()

data.to_hdf("./daily_pv_all.h5", key="data")


fields = ["$open", "$close", "$high", "$low", "$volume", "$factor"]
debug = D.features(instruments, fields, start_time="2018-01-01", end_time="2019-12-31", freq="day").swaplevel().sort_index()
# The first 100 instruments that trade in the debug window (index members may have left before it).
keep = debug.index.get_level_values("instrument").unique()[:100]
debug = debug[debug.index.get_level_values("instrument").isin(keep)]

debug.to_hdf("./daily_pv_debug.h5", key="data")
