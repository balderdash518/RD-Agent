# A-share 中证1000 profile for the 2026-11 unified experiments (fin_factor). Load on top of .env:
#   set -a; source rdagent/scenarios/qlib/market_profiles/cn_csi1000.profile; set +a
#   rdagent fin_factor --loop-n 36
#
# Data split, model and factor source data (instruments=all, shared by all pools) come from .env:
# train 2016-2020, valid 2021, RD-Agent selection (test) segment 2021; 2022-2025 is only used by the final unified evaluation.
# Data: ~/.qlib/qlib_data/cn_data -> cn_data_20261025 (chenditc 2026-10-25, sha256 0a2974da97b4...).

QLIB_FACTOR_MARKET=csi1000
QLIB_FACTOR_BENCHMARK=SH000852
QLIB_MODEL_MARKET=csi1000
QLIB_MODEL_BENCHMARK=SH000852
QLIB_QUANT_MARKET=csi1000
QLIB_QUANT_BENCHMARK=SH000852
