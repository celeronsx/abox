# ADR-0003: Agentic retrieval, nomic via llama.cpp against all-MiniLM via the official Qdrant MCP

- Status: Accepted, measured on this cluster on 2026-09-16, re-measured after the MCP fix the same day
- Date: 2026-09-16
- Related: [ADR-0001](./0001-text-embedding-model.md), [ADR-0002](./0002-embedding-runtime-in-cluster.md), TODO `docs/todo/agentic-retrieval-eval.md`, data in `docs/eval/`

## Context

Lab 4 asks to index the same data through two Qdrant MCP servers and compare Agentic
Retrieval quality:

| Route | MCP server | Embedder | Dims | Collection | Tools |
|---|---|---|---|---|---|
| A | `qdrant-mcp`, the Go server from upstream `mcp/qdrant-mcp` (image `ghcr.io/den-vasyliev/abox/qdrant-mcp:0.4.0`) | nomic-embed-text-v1.5 over HTTP to `llama-cpp-embeddings.llama-cpp:8090` (ADR-0001, ADR-0002) | 768 | `abox-nomic` | `vector_store`, `vector_find` |
| B | `qdrant-mcp-official`, `uvx mcp-server-qdrant==0.8.1` run by kmcp | fastembed in-process, `sentence-transformers/all-MiniLM-L6-v2` | 384 | `abox-minilm` | `qdrant-store`, `qdrant-find` |

Two kagent agents, `retrieval-agent` (A) and `retrieval-agent-official` (B), identical apart
from the vector tool names in the prompt and the MCPServer they point at. Both on
`ModelConfig/litellm-gateway` (claude-sonnet-5 through LiteLLM), both with `stream: false`,
see Findings. Same Neo4j graph tools on both, not used in the measurement.

Corpus: 8 kagent custom resources from namespace `kagent`, 3 MCPServer, 2 ModelConfig,
3 Agent, exported with `kubectl get -o json`, stripped of `status`, `managedFields`, `uid`,
`resourceVersion`, `creationTimestamp`, `generation`, `annotations`. 352 to 8116 characters
each. Gold set: 9 questions in `docs/eval/gold.json`, 8 with the document that answers them and
the exact values the answer must quote, plus one negative control whose subject (Weaviate) does
not appear in the corpus at all.

## Measurements

### Retrieval only, no LLM

Each gold question embedded with the collection's own model (nomic through the same llama.cpp
GGUF, MiniLM through fastembed), searched in Qdrant, rank of the expected document recorded.
`docs/eval/retrieval_bench.py`, output in `docs/eval/retrieval-bench.txt`.

| Route | Recall@1 | Recall@3 | MRR | Points stored for 8 docs |
|---|---|---|---|---|
| A nomic-768 | 6/8 | 8/8 | 0.854 | 21, the Go server chunks by `maxInputChars` |
| B MiniLM-384 | 6/8 | 7/8 | 0.830 | 8, one point per document, named vector `fast-all-minilm-l6-v2` |

Where they differ: q4 (which ModelConfig uses gpt-4.1-mini) ranks 2 on nomic and 7 on MiniLM.
q7 (promql-agent, an 8 KB manifest) ranks 3 on nomic and 1 on MiniLM. Top-1 cosine scores
are systematically lower on MiniLM (0.17 to 0.74 against 0.65 to 0.84), which is expected
from a 384-dim model truncating at 256 word pieces, but on this corpus the ordering mostly
survives.

### Agent level, vector only

Each agent got the gold questions with an instruction to use only the vector tool. A hit
requires every value in `must` to appear in the answer; the negative control (q9) passes only
when the agent says the data is not there. `docs/eval/eval.py`, results in
`docs/eval/eval-results.json`.

| Route | Before the MCP fix | After the MCP fix | Avg time per question |
|---|---|---|---|
| A nomic via `retrieval-agent` | 0/8 | **9/9** | 23.8 s to 20.2 s |
| B MiniLM via `retrieval-agent-official` | 7/8 | **9/9** | 11.3 s to 14.3 s |

Route A scored zero before the fix and not because retrieval failed: the MCP log showed
`vector_find` returning documents in 5 to 18 ms for every query while the agent reported an
empty store. Finding 1 explains why, and the fix is verified below.

## Findings

1. **The Go MCP's tool results never reached the model, and the cause is one unset field.**
   Every tool in `mcp/qdrant-mcp` is registered as `MCPTool[_, Raw]` where `Raw` is
   `{Body string}`, so the Go SDK derives an `outputSchema` of `{"body": string}` and always
   serialises `StructuredContent` (encoding/json's `omitempty` does not drop a struct). The
   shared `text()` helper filled only `Content`, leaving `structuredContent: {"body": ""}` next
   to a full text block. A client that honours the declared schema, which kagent does, reads the
   empty body. The MCP spec for 2025-06-18 requires that a server providing an output schema
   return structured results conforming to it, and an empty body next to real data does not.
   Fixed by filling the field the author's own comment already describes as "the upstream JSON,
   untouched":

   ```go
   return &mcp.CallToolResultFor[Raw]{
       Content:           []mcp.Content{&mcp.TextContent{Text: s}},
       StructuredContent: Raw{Body: s},
   }
   ```

   Verified in three steps: a regression test that fails on the original code and passes on the
   patched one, a raw `tools/call` against the rebuilt server returning
   `structuredContent.body` of 805 characters instead of 0, and the agent-level table above
   going from 0/8 to 9/9 with nothing else changed.

2. **Large tool-call arguments through LiteLLM are fragile.** With `stream: true` the model's
   calls to `qdrant-store` with a full manifest arrived as `{}` and failed validation on
   `information`, 5 of 7 calls in one run. With `stream: false` the run ended silently after the
   fetch phase with an empty final message. A single store per A2A message with a 5 to 8 KB
   argument works every time in both modes. Bulk agentic ingest of many large documents in one
   turn is not reliable on this path.
3. **A blocking A2A call is the task's lifetime.** kagent cancels the task when the HTTP client
   disconnects. A 900 s client timeout killed both first ingest runs at exactly 901 s. Long
   ingests need a client that stays connected, or one small task per message.
4. **The two routes store different shapes.** A chunks (`chunk`, `chunks`, `doc` in payload,
   flat fields `kind`, `name`, `namespace`), B stores one point with `document` and a nested
   `metadata` object and a named vector. Anything that reads Qdrant directly has to know which
   one it is talking to.
5. **The grader was wrong before the agent was.** The negative control first scored as a miss
   for route B. The agent had refused correctly, with "found no MCPServer configured with a
   Weaviate backend", and the substring list in `eval.py` simply did not contain that phrasing.
   Re-scored from the stored answers rather than re-running the agents. A keyword matcher over
   free text is a weak grader and its misses look exactly like model failures, which is the
   argument for moving this to an LLM judge in Phoenix.
6. **kmcp's stdio adapter needs inotify headroom.** The official server's pod crash-looped with
   `Failed to create file watcher: No file descriptors available` until
   `fs.inotify.max_user_instances` on the kind nodes went from 128 to 8192.

## Decision

1. Keep **route A, nomic-768 through llama.cpp,** as the retrieval path for abox. It is the
   better retriever on this corpus (Recall@3 8/8, MRR 0.854), it keeps the 8192-token window
   from ADR-0001, and it is the only route that lets the embedder be swapped or scaled without
   touching the MCP server.
2. Route A is usable again after the one-line fix in `text()`. The patch lives on the branch
   `fix/mcp-structured-content` off upstream `feat/llmd-embeddings` and goes upstream as a pull
   request, not as a fork-local patch, because every student running this lab hits it.
3. Ingest is **one document per agent message**, not one instruction for the whole corpus. The
   runbook in the TODO does it that way.
4. **`stream: false` on both retrieval agents**, kept until the tool-argument loss on the
   streaming path is understood.

## Consequences

- The comparison had to be run at two levels to be trustworthy. On the agent level alone, route
  A scored 0/8 and route B 7/8, which reads as "MiniLM wins" and is wrong: the direct benchmark
  showed A ahead on Recall@3 and MRR, and the gap was a serialisation bug. After the fix both
  routes answer every question, so on this corpus the agent level no longer separates them at
  all and the retrieval numbers are the only thing that does.
- Nine questions over eight documents is a smoke test. It caught a broken path and a bad
  grader, which is what it is for. It cannot rank two working retrievers with confidence.
- `abox-nomic` and `abox-minilm` both stay in Qdrant for now, the eval scripts read both.
- Next: grow the gold set, and move scoring into Phoenix experiments so trajectory (which tool,
  how many calls, did it touch the graph when told not to) is scored instead of only the final
  text.
- Tokenomics for this lab is not recorded. Every agent call in this ADR went through LiteLLM,
  where the per-key spend is visible, and Phoenix is not yet receiving kagent traces. Both are
  open items in ADR-0001.
