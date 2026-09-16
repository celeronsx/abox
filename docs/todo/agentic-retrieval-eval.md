# TODO: ingest the same data through two Qdrant MCP routes and compare retrieval

Implements [ADR-0003](../adr/0003-agentic-retrieval-evaluation.md). Agent-executable. Run on
2026-09-16 against the abox KinD cluster. Scripts and results live in `docs/eval/`.

Done means section 7 passes.

## 1. Prerequisites

- Cluster with `releases/` of this fork applied, so `qdrant-mcp`, `qdrant-mcp-official`,
  `neo4j-mcp`, `retrieval-agent`, `retrieval-agent-official` exist in namespace `kagent`.
- A ModelConfig with a working key on both retrieval agents. This fork ships them on
  `default-model-config`, whose secret is a placeholder. Point them at your own:

```bash
kubectl patch agent retrieval-agent -n kagent --type merge -p '{"spec":{"declarative":{"modelConfig":"<your-modelconfig>"}}}'
kubectl patch agent retrieval-agent-official -n kagent --type merge -p '{"spec":{"declarative":{"modelConfig":"<your-modelconfig>"}}}'
```

- `kubectl port-forward -n kagent svc/kagent-controller 8083:8083` running, the A2A endpoint is
  `http://localhost:8083/api/a2a/kagent/<agent>/`.
- `kubectl port-forward -n qdrant svc/qdrant 6333:6333` for the direct benchmark.
- The local llama.cpp embedder from `docs/todo/embeddings-local-llama-cpp.md` on `localhost:8088`,
  same Q8_0 GGUF as the cluster, so query vectors match the collection.
- `uv` on the host, fastembed is pulled on demand.

## 2. If `qdrant-mcp-official` crash-loops

Symptom in the `mcp-server` container log, which is the kmcp agentgateway adapter:

```
Error: Failed to create file watcher: No file descriptors available (os error 24)
```

The kind nodes ship `fs.inotify.max_user_instances = 128` and the Flux controllers, kagent and
the other MCP adapters use them up. Raise it on every node, the pod recovers on its own:

```bash
for n in abox-control-plane abox-worker abox-worker2; do
  docker exec $n sysctl -w fs.inotify.max_user_instances=8192
done
```

## 3. Export the corpus

Eight kagent objects, stripped of fields that change on every read:

```bash
mkdir -p docs/eval/objects
for spec in mcpserver/neo4j-mcp mcpserver/qdrant-mcp mcpserver/qdrant-mcp-official \
            modelconfig/default-model-config modelconfig/litellm-gateway \
            agent/retrieval-agent agent/retrieval-agent-official agent/promql-agent; do
  kind=${spec%%/*}; name=${spec#*/}
  kubectl get $kind $name -n kagent -o json | python3 -c '
import json,sys
o=json.load(sys.stdin); o.pop("status",None)
for k in ("managedFields","uid","resourceVersion","creationTimestamp","generation","annotations"): o["metadata"].pop(k,None)
json.dump(o, open(sys.argv[1],"w"), indent=1)' "docs/eval/objects/$kind-$name.json"
done
```

## 4. Ingest, one document per message

Do not ask the agent to ingest the whole list in one instruction. With `stream: true` the store
calls arrive as `{}` and fail validation, with `stream: false` the task ends silently after the
fetch phase, and a blocking client that times out kills the task at that second. One document
per A2A message with the manifest inline works on both agents every time, 8 to 55 s each.

```bash
docs/eval/ingest_one.sh retrieval-agent          vector_store docs/eval/objects docs/eval/ingest-nomic.log
docs/eval/ingest_one.sh retrieval-agent-official qdrant-store docs/eval/objects docs/eval/ingest-minilm.log
```

Expected after both finish:

```bash
curl -s localhost:6333/collections/abox-nomic  | jq '.result.points_count'   # 21, chunked
curl -s localhost:6333/collections/abox-minilm | jq '.result.points_count'   # 8
```

## 5. Direct retrieval benchmark

```bash
cd docs/eval && uv run --with fastembed python retrieval_bench.py | tee retrieval-bench.txt
```

Two things the script has to know that are not obvious from the outside: the MiniLM collection
uses a named vector `fast-all-minilm-l6-v2`, so the query carries `"using"`, and nomic queries
need the `search_query: ` prefix while documents were stored with `search_document: `.

Result on 2026-09-16: nomic Recall@1 6/8, Recall@3 8/8, MRR 0.854. MiniLM Recall@1 6/8,
Recall@3 7/8, MRR 0.830.

## 6. Agent-level evaluation

```bash
python3 docs/eval/eval.py docs/eval/eval-results.json
```

Each question goes to both agents with "answer from the vector store only". A hit requires every
value in `must` to appear in the answer.

Result on 2026-09-16 with the patched Go MCP: both agents 9/9, including the negative control.
Before the patch `retrieval-agent` scored 0/8. Before reading any 0/N as a retrieval result,
check the MCP log:

```bash
kubectl logs -n kagent deploy/qdrant-mcp -c mcp-server | grep vector_find | tail
```

If the server logs hits and the agent reports none, you are looking at ADR-0003 Finding 1, the
`structuredContent: {"body": ""}` in the Go server's responses. The `body` must carry the same
JSON as the text block; if it is empty, the image predates the fix. Verify with a raw call:

```bash
kubectl run mcpq --rm -i --restart=Never -n kagent --image=curlimages/curl:8.11.0 -q -- sh -c '
H="Content-Type: application/json"; A="Accept: application/json, text/event-stream"; U=http://qdrant-mcp.kagent:3000/mcp
SID=$(curl -s -X POST $U -H "$H" -H "$A" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"0\"}}}" -D - -o /dev/null | grep -i "^mcp-session-id" | awk "{print \$2}" | tr -d "\r")
curl -s -X POST $U -H "$H" -H "$A" -H "mcp-session-id: $SID" -d "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"vector_find\",\"arguments\":{\"query\":\"neo4j\",\"limit\":1}}}"' | grep -o '"structuredContent":{[^}]*}'
```

## 7. Definition of done

- [ ] both collections exist with the expected point counts
- [ ] `retrieval-bench.txt` has a Recall and MRR line for both routes
- [ ] `eval-results.json` has 8 rows per agent
- [ ] any 0/N at agent level is explained by a log, not reported as retrieval quality
- [ ] the negative control is answered with an explicit "not in the store" by both agents
- [ ] tokens spent recorded, or the reason they were not
