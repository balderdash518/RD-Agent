#!/bin/bash
# Noise-floor control for run 2026-10-22_16-11-01 (US S&P 500): rerun the Loop 10 SOTA factor set unchanged,
# then with one extra pure-noise column, and record with-cost annualized return / IC / best iteration.
set -u
SRC=/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/RD-Agent_workspace/0c97f0dd145a430cbdd9b52e6dfa070f
PY=/home/yifei/.venvs/rdagent/bin/python
ROOT=/tmp/rd_noise_us
OUT=${1:-$(cd "$(dirname "$0")" && pwd)}/us_noise_floor.txt
mkdir -p $ROOT
LOGLINE=$(grep -a -m1 "Render the template" $SRC/logs/docker_execution_20261023_013816.log)
$PY - "$LOGLINE" > $ROOT/env.list <<'PYEOF'
import ast, sys
ctx = ast.literal_eval(sys.argv[1].split("context: ", 1)[1])
ctx["PYTHONPATH"] = "./"
ctx["MLFLOW_ALLOW_FILE_STORE"] = "true"
for k, v in ctx.items():
    print(f"{k}={v}")
PYEOF
: > $OUT
for name in repro noise1 noise2 noise3 noise4 noise5; do
  W=$ROOT/$name; rm -rf $W; mkdir -p $W
  cp $SRC/conf_combined_factors.yaml $SRC/read_exp_res.py $W/
  $PY - $SRC/combined_factors_df.parquet $W/combined_factors_df.parquet $name <<'PYEOF'
import sys, numpy as np, pandas as pd
src, dst, name = sys.argv[1:]
df = pd.read_parquet(src)
if name.startswith("noise"):
    rng = np.random.default_rng(int(name[-1]))
    df[("feature", "pure_noise")] = rng.standard_normal(len(df))
df.to_parquet(dst)
PYEOF
  docker run --rm --env-file $ROOT/env.list -v $W:/workspace/qlib_workspace -v $HOME/.qlib:/root/.qlib \
    -w /workspace/qlib_workspace local_qlib:latest /bin/sh -c 'qrun conf_combined_factors.yaml > qrun.log 2>&1; python read_exp_res.py > /dev/null 2>&1; chmod -R 777 .'
  m=$(grep -E "^IC,|^Rank IC,|^1day.excess_return_with_cost.annualized_return,|^1day.excess_return_with_cost.max_drawdown," $W/qlib_res.csv | tr '\n' ' ')
  bi=$(grep -a -A1 "Early stopping, best iteration" $W/qrun.log | tail -1 | tr '\t' ' ')
  echo "$name | $m | $bi" >> $OUT
done
echo done >> $OUT
