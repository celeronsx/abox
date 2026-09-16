#!/usr/bin/env python3
"""Send the gold questions to both retrieval agents, vector-only, and score them.

usage: eval.py <out.json>
Scores a question as hit when every `must` substring appears in the answer (case-insensitive).
"""
import json, subprocess, sys, time, pathlib

HERE = pathlib.Path(__file__).parent
GOLD = json.loads((HERE / "gold.json").read_text())
AGENTS = {
    "nomic-768 (qdrant-mcp, llama.cpp)": "retrieval-agent",
    "minilm-384 (official mcp-server-qdrant)": "retrieval-agent-official",
}
PREFIX = ("Answer from the vector store only: use the vector search tool, do not call the graph, "
          "do not delegate, do not ingest. Quote the exact values you find. Question: ")

def ask(agent, text, timeout=300):
    t = time.perf_counter()
    p = subprocess.run([str(HERE / "a2a.sh"), agent, text, str(timeout)], capture_output=True, text=True, timeout=timeout + 30)
    return time.perf_counter() - t, (p.stdout or "") + (p.stderr or "")

results = {}
for label, agent in AGENTS.items():
    rows = []
    for g in GOLD:
        dt, ans = ask(agent, PREFIX + g["q"])
        low = ans.lower()
        if g["must"] == ["__NO_ANSWER__"]:
            # negative control: the corpus has no answer, so a pass is an explicit "not found"
            # Free-text refusal comes in many shapes. The first version of this list
            # missed "found no MCPServer ..." and scored a correct refusal as a miss,
            # so the grader, not the agent, was wrong. Keep it broad and re-score from
            # the stored answers rather than re-running the agents.
            refused = any(p in low for p in (
                "not in the", "no result", "nothing", "not found", "found no",
                "does not contain", "no such", "cannot answer", "not present",
                "no mcpserver", "no agent", "no modelconfig", "is not configured",
                "there is no", "are no ", "absent",
            ))
            rows.append({"id": g["id"], "hit": refused, "found": ["refused"] if refused else [], "missing": [] if refused else ["explicit not-found"], "sec": round(dt, 1), "answer": ans[-1200:]})
        else:
            found = [m for m in g["must"] if m.lower() in low]
            rows.append({"id": g["id"], "hit": len(found) == len(g["must"]), "found": found, "missing": [m for m in g["must"] if m not in found], "sec": round(dt, 1), "answer": ans[-1200:]})
        print(f"{label[:12]:12s} {g['id']} hit={rows[-1]['hit']!s:5s} {dt:6.1f}s missing={rows[-1]['missing']}", flush=True)
    hits = sum(r["hit"] for r in rows)
    results[label] = {"agent": agent, "hits": hits, "total": len(rows), "avg_sec": round(sum(r["sec"] for r in rows) / len(rows), 1), "rows": rows}
    print(f"== {label}: {hits}/{len(rows)} hits, avg {results[label]['avg_sec']}s", flush=True)

pathlib.Path(sys.argv[1]).write_text(json.dumps(results, indent=1, ensure_ascii=False))
print("saved", sys.argv[1])
