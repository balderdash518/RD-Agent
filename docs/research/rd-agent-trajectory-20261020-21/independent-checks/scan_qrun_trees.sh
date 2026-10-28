cd /home/yifei/projects/RD-Agent-codex-repro/git_ignore_folder/RD-Agent_workspace
for f in */logs/docker_execution_2*.log; do
  grep -a -q "qrun conf_" "$f" || continue
  ts=$(grep -a -m1 "^Timestamp:" "$f" | cut -d' ' -f2-3)
  conf=$(grep -a -m1 -o "qrun conf_[a-z_]*.yaml" "$f")
  ts0=$(grep -a -m1 -o "'test_start': '[0-9-]*'" "$f" | cut -d"'" -f4)
  lr=$(grep -a -m1 -o "learning_rate[^,]*" "$f" | head -1)
  bi=$(grep -a -A1 "Early stopping, best iteration" "$f" | tail -1 | grep -o "^\[[0-9]*\]")
  [ -z "$bi" ] && bi=$(grep -a -o "Did not meet early stopping" "$f" | head -1)
  echo "$ts|${f%%/*}|$conf|$ts0|$bi"
done | sort
