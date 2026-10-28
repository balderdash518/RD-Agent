#!/bin/bash
# Noise-floor control: rerun Loop_8 SOTA config unchanged, then with one extra pure-noise column.
set -u
SRC=/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/RD-Agent_workspace/14d8637a12b547a8b90f8abe5751d529
PY=/home/yifei/.venvs/rdagent/bin/python
ROOT=/tmp/rd_noise
OUT=${1:-$(cd "$(dirname "$0")" && pwd)}/results.txt
mkdir -p $ROOT
LOGLINE=$(grep -a -m1 "Render the template" $SRC/logs/docker_execution_20261021_004436.log)
$PY - "$LOGLINE" > $ROOT/env.list <<'EOF'
import ast, sys
ctx = ast.literal_eval(sys.argv[1].split("context: ", 1)[1])
ctx["PYTHONPATH"] = "./"
ctx["MLFLOW_ALLOW_FILE_STORE"] = "true"
for k, v in ctx.items():
    print(f"{k}={v}")
EOF
: > $OUT
for name in repro noise1 noise2 noise3 shuffle1; do
  W=$ROOT/$name; rm -rf $W; mkdir -p $W
  cp $SRC/conf_combined_factors.yaml $SRC/read_exp_res.py $W/
  $PY - $SRC/combined_factors_df.parquet $W/combined_factors_df.parquet $name <<'EOF'
import sys, numpy as np, pandas as pd
src, dst, name = sys.argv[1:]
df = pd.read_parquet(src)
if name.startswith("noise"):
    rng = np.random.default_rng(int(name[-1]))
    df[("feature", "pure_noise")] = rng.standard_normal(len(df))
elif name.startswith("shuffle"):
    # same marginal distribution as a real factor, but no link to any stock/date
    rng = np.random.default_rng(int(name[-1]) + 100)
    col = df[("feature", "vwap_deviation_10d_pctrank")].to_numpy().copy()
    rng.shuffle(col)
    df[("feature", "shuffled_vwap")] = col
df.to_parquet(dst)
EOF
  docker run --rm --env-file $ROOT/env.list -v $W:/workspace/qlib_workspace -v $HOME/.qlib:/root/.qlib \
    -w /workspace/qlib_workspace local_qlib:latest /bin/sh -c 'qrun conf_combined_factors.yaml > qrun.log 2>&1; python read_exp_res.py > /dev/null 2>&1; chmod -R 777 .'
  ic=$(grep -E "^IC,|^Rank IC," $W/qlib_res.csv | tr '\n' ' ')
  bi=$(grep -a -A1 "Early stopping, best iteration" $W/qrun.log | tail -1 | tr '\t' ' ')
  echo "$name | $ic | $bi" >> $OUT
done
echo done >> $OUT
