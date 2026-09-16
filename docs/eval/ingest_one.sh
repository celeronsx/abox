#!/usr/bin/env bash
# usage: ingest_one.sh <agent> <storetool> <objects_dir> <out_log>
agent="$1"; tool="$2"; dir="$3"; out="$4"; S="$(dirname "$0")"
: > "$out"
for f in "$dir"/*.json; do
  base=$(basename "$f" .json)
  yaml=$(uv run --quiet --with pyyaml python -c 'import json,sys,yaml; print(yaml.safe_dump(json.load(open(sys.argv[1])),sort_keys=False))' "$f")
  kind=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["kind"])' "$f")
  name=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["metadata"]["name"])' "$f")
  msg="Store the following Kubernetes manifest in the vector store with ONE ${tool} call. Put the complete manifest text below as the information, unchanged. Use metadata {\"kind\":\"${kind}\",\"name\":\"${name}\",\"namespace\":\"kagent\"}. Do not use any other tool. Reply only with the tool result.

${yaml}"
  start=$(date +%s)
  res=$(A2A_RAW="$out.$base.raw.json" "$S/a2a.sh" "$agent" "$msg" 300 2>&1)
  echo "[$base] $(( $(date +%s)-start ))s :: $(echo "$res" | tr '\n' ' ' | cut -c1-200)" | tee -a "$out"
done
echo "ALL DONE" | tee -a "$out"
