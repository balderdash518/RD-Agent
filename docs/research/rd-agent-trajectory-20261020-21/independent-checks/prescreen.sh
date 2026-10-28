#!/bin/bash
# Add factors chosen by a train/valid-only prescreen rule to the Loop_8 SOTA set and rerun the same qrun.
set -u
SRC=/home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/RD-Agent_workspace/14d8637a12b547a8b90f8abe5751d529
PY=/home/yifei/.venvs/rdagent/bin/python
ROOT=/tmp/rd_noise
S=${1:-$(cd "$(dirname "$0")" && pwd)}
OUT=$S/results.txt

for name in prescreen5 prescreen1; do
  W=$ROOT/$name; rm -rf $W; mkdir -p $W
  cp $SRC/conf_combined_factors.yaml $SRC/read_exp_res.py $W/
  $PY - $SRC/combined_factors_df.parquet $W/combined_factors_df.parquet $name $S/prescreen.json <<'EOF'
import json, sys, pandas as pd
src, dst, name, sel = sys.argv[1:]
df = pd.read_parquet(src)
facs = json.load(open(sel))
if name == "prescreen1":
    facs = [f for f in facs if f["name"] == "idiosyncratic_volatility_20d_pctrank"]
for f in facs:
    s = pd.read_hdf(f"{f['path']}/result.h5").iloc[:, 0]
    if s.index.names[0] != "datetime":
        s.index = s.index.reorder_levels(["datetime", "instrument"])
    s = s[~s.index.duplicated()]
    df[("feature", f["name"])] = s.reindex(df.index).astype("float64")
print(df.columns.tolist())
df.to_parquet(dst)
EOF
  docker run --rm --env-file $ROOT/env.list -v $W:/workspace/qlib_workspace -v $HOME/.qlib:/root/.qlib \
    -w /workspace/qlib_workspace local_qlib:latest /bin/sh -c 'qrun conf_combined_factors.yaml > qrun.log 2>&1; python read_exp_res.py > /dev/null 2>&1; chmod -R 777 .'
  ic=$(grep -E "^IC,|^Rank IC,|^1day.excess_return_with_cost.annualized_return," $W/qlib_res.csv | tr '\n' ' ')
  bi=$(grep -a -A1 "Early stopping, best iteration" $W/qrun.log | tail -1 | tr '\t' ' ')
  echo "$name | $ic | $bi" >> $OUT
done
echo done >> $OUT
