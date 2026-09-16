#!/usr/bin/env bash
# usage: a2a.sh <agent> <text> [timeout_sec]   -> prints final text, exit 1 on error
agent="$1"; text="$2"; to="${3:-3600}"
payload=$(python3 -c 'import json,sys; print(json.dumps({"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"role":"user","messageId":"m-"+str(abs(hash(sys.argv[1]))%10**8),"parts":[{"kind":"text","text":sys.argv[1]}]}}}))' "$text")
curl -s -m "$to" "localhost:8083/api/a2a/kagent/${agent}/" -H 'Content-Type: application/json' -d "$payload" | tee "${A2A_RAW:-/dev/null}" | python3 -c '
import json,sys
raw=sys.stdin.read()
try: d=json.loads(raw)
except Exception: print("NONJSON:", raw[:500]); sys.exit(1)
if "error" in d: print("ERROR:", json.dumps(d["error"])[:1500]); sys.exit(1)
r=d["result"]; out=[]
for a in r.get("artifacts",[]): out += [p.get("text","") for p in a.get("parts",[]) if p.get("kind")=="text"]
if not out and r.get("status",{}).get("message"): out=[p.get("text","") for p in r["status"]["message"].get("parts",[])]
print("STATE:", r.get("status",{}).get("state")); print("\n".join(out))'
