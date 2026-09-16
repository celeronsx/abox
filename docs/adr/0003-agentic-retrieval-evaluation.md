# ADR-0003: Agentic retrieval, nomic via llama.cpp against all-MiniLM via the official Qdrant MCP

- Status: Accepted, measured on this cluster on 2026-09-16
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
each. Gold set: 8 questions, each with the document that answers it and the exact values the
answer must quote (`docs/eval/gold.json`).

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

Each agent got the 8 questions with an instruction to use only the vector tool. Hit means every
required value appears in the answer. `docs/eval/eval.py`, output `docs/eval/eval-results.json`.

| Route | Hits | Avg time per question |
|---|---|---|
| A nomic via `retrieval-agent` | 0/8 | 23.8 s |
| B MiniLM via `retrieval-agent-official` | 8/8 after correcting one gold value, 7/8 as scored | 11.3 s |

Route A scored zero not because retrieval failed. The MCP log shows `vector_find` returning
documents in 5 to 18 ms for every query the agent sent. The agent reported "the vector store
returned no results" each time. See Finding 1.

## Findings

1. **The Go MCP's tool results never reach the model.** Every `vector_store` and `vector_find`
   response carries `structuredContent: {"body": ""}` next to a full `content[0].text`. kagent's
   MCP toolset prefers `structuredContent` when present, so the model sees an empty body:
   `{"output":{"body":""}}` on store, "no results" on find. Reproduced with a direct
   `tools/call` against `qdrant-mcp.kagent:3000/mcp`. The official server sends text only and
   works. This is a bug in `mcp/qdrant-mcp` (the `Raw` result type), not in the embedder or the
   agent. It is also why upstream's own ingest instruction, "the collection is chosen by the
   server, not by you", produced an agent that could write but never read.
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
5. **kmcp's stdio adapter needs inotify headroom.** The official server's pod crash-looped with
   `Failed to create file watcher: No file descriptors available` until
   `fs.inotify.max_user_instances` on the kind nodes went from 128 to 8192.

## Decision

1. Keep **route A, nomic-768 through llama.cpp,** as the retrieval path for abox. It is the
   better retriever on this corpus (Recall@3 8/8, MRR 0.854), it keeps the 8192-token window
   from ADR-0001, and it is the only route that lets the embedder be swapped or scaled without
   touching the MCP server.
2. Route A is **not usable from a kagent agent until the `structuredContent` bug is fixed** in
   `mcp/qdrant-mcp`. Until then `retrieval-agent-official` is the agent that answers, and the
   fix goes upstream as a pull request rather than a fork-local patch.
3. Ingest is **one document per agent message**, not one instruction for the whole corpus. The
   runbook in the TODO does it that way.
4. **`stream: false` on both retrieval agents**, kept until the tool-argument loss on the
   streaming path is understood.

## Consequences

- The certificate lab's comparison is answered at two levels and they disagree on purpose: the
  retriever that wins on Recall (A) is the one whose agent scores 0/8, and the reason is a
  serialisation bug, not embeddings. Reporting only the agent-level number would have picked
  the wrong model.
- `abox-nomic` and `abox-minilm` both stay in Qdrant for now, the eval scripts read both.
- The gold set has 8 questions. It separates the two routes on two questions and is enough to
  catch a broken path, not enough to rank two working ones with confidence. Growing it is the
  next step, together with running the same questions through Phoenix experiments so
  trajectory (which tool, how many calls) is scored, not only the final text.
- Tokenomics for this lab is not recorded. Every agent call in this ADR went through LiteLLM,
  where the per-key spend is visible, and Phoenix is not yet receiving kagent traces. Both are
  open items in ADR-0001.
