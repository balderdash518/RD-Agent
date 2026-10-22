# US S&P 500 market profile for the qlib scenarios (fin_factor / fin_model / fin_quant).
#
# Load it on top of .env; shell variables take precedence over .env, so the CN settings there stay intact:
#   set -a; source rdagent/scenarios/qlib/market_profiles/us_sp500.profile; set +a
#   rdagent fin_factor --loop-n 1
#
# Benchmark ^SP500TR is the total-return S&P 500: stock returns include dividends, so a price index would
# overstate excess returns by the dividend yield. ^GSPC (price index) is also in the data.
#
# Data: ~/.qlib/qlib_data/us_data, built by tools/us_sp500/build_us_sp500.py (point-in-time S&P 500,
# 2005-01 .. latest). Keep "~" quoted: qlib runs in Docker where ~/.qlib is mounted at /root/.qlib.
#
# Split mirrors the CN setup: the framework selects factors on the test segment (2020-2022);
# 2023-01-01 onwards is a holdout for one final evaluation only.

QLIB_FACTOR_PROVIDER_URI="~/.qlib/qlib_data/us_data"
QLIB_FACTOR_REGION=us
QLIB_FACTOR_MARKET=sp500
QLIB_FACTOR_BENCHMARK=^SP500TR
QLIB_FACTOR_LIMIT_THRESHOLD=None
QLIB_FACTOR_DEAL_PRICE=close
# No stamp duty; 5 bp per side approximates spread + impact for large caps. Commission is zero at most brokers.
QLIB_FACTOR_OPEN_COST=0.0005
QLIB_FACTOR_CLOSE_COST=0.0005
QLIB_FACTOR_MIN_COST=0
QLIB_FACTOR_TRAIN_START=2010-01-01
QLIB_FACTOR_TRAIN_END=2017-12-31
QLIB_FACTOR_VALID_START=2018-01-01
QLIB_FACTOR_VALID_END=2019-12-31
QLIB_FACTOR_TEST_START=2020-01-01
QLIB_FACTOR_TEST_END=2022-12-31

QLIB_MODEL_PROVIDER_URI="~/.qlib/qlib_data/us_data"
QLIB_MODEL_REGION=us
QLIB_MODEL_MARKET=sp500
QLIB_MODEL_BENCHMARK=^SP500TR
QLIB_MODEL_LIMIT_THRESHOLD=None
QLIB_MODEL_DEAL_PRICE=close
QLIB_MODEL_OPEN_COST=0.0005
QLIB_MODEL_CLOSE_COST=0.0005
QLIB_MODEL_MIN_COST=0
QLIB_MODEL_TRAIN_START=2010-01-01
QLIB_MODEL_TRAIN_END=2017-12-31
QLIB_MODEL_VALID_START=2018-01-01
QLIB_MODEL_VALID_END=2019-12-31
QLIB_MODEL_TEST_START=2020-01-01
QLIB_MODEL_TEST_END=2022-12-31

QLIB_QUANT_PROVIDER_URI="~/.qlib/qlib_data/us_data"
QLIB_QUANT_REGION=us
QLIB_QUANT_MARKET=sp500
QLIB_QUANT_BENCHMARK=^SP500TR
QLIB_QUANT_LIMIT_THRESHOLD=None
QLIB_QUANT_DEAL_PRICE=close
QLIB_QUANT_OPEN_COST=0.0005
QLIB_QUANT_CLOSE_COST=0.0005
QLIB_QUANT_MIN_COST=0
QLIB_QUANT_TRAIN_START=2010-01-01
QLIB_QUANT_TRAIN_END=2017-12-31
QLIB_QUANT_VALID_START=2018-01-01
QLIB_QUANT_VALID_END=2019-12-31
QLIB_QUANT_TEST_START=2020-01-01
QLIB_QUANT_TEST_END=2022-12-31

# Factor-coding source data is kept separate from the CN files.
FACTOR_CoSTEER_DATA_FOLDER=git_ignore_folder/factor_implementation_source_data_us
FACTOR_CoSTEER_DATA_FOLDER_DEBUG=git_ignore_folder/factor_implementation_source_data_debug_us
FACTOR_CoSTEER_SOURCE_DATA_INSTRUMENTS=sp500
