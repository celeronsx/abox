# ADR-0001: Text embedding model for repository code and docs

- Status: Accepted, validated by a local run on 2026-09-16, see the TODO
- Date: 2026-09-16
- Related: Lab 3 (Harness Engineering, fwdays), TODO `docs/todo/embeddings-local-llama-cpp.md`

## Context

Lab 3 asks to prepare the repository (code and documentation) for vectorization, so that
agents can retrieve relevant context through semantic search instead of loading large parts
of the codebase into the model context. Text only. The corpus for this decision is English:
the abox fork itself, its Markdown docs, Kubernetes manifests, OpenTofu and shell.

Where the model runs and what bounds the choice:

| Constraint | Consequence |
|---|---|
| KinD on a MacBook (arm64), Docker VM with 6 CPU and 16 GiB, no GPU | CPU inference is the baseline, the model has to fit next to agentgateway, kagent, Qdrant, Phoenix |
| Flux reconciles from an OCI artifact, no sudo on the host | Runtime must be a plain container with a pinned model, no host installs |
| Served through agentgateway (Gateway API) | Needs an HTTP service, ideally OpenAI-shaped `/v1/embeddings` |
| Course requires a local run with llama.cpp or Ollama, then sidecar and llm-d in cluster | GGUF build must exist for the model |
| Repository is Apache 2.0 | Model licence must be permissive and not gated |
| Vector dimension is a lock-in, changing the model later means re-embedding everything | Prefer a model that lets us shrink dimensions without switching models |

## Options

Per the course method: one cloud-provider model, one mid-size self-hosted, one small model
with Matryoshka Representation Learning (MRL). Facts below are taken from the model cards and
AWS docs on 2026-09-16.

| | Titan Text Embeddings V2 | nomic-embed-text-v1.5 | EmbeddingGemma-300M |
|---|---|---|---|
| Role | Cloud, we already run on AWS and Bedrock AgentCore | Mid self-hosted, abox default | Small MRL |
| Params | not published | 137M | 300M |
| Context | 8192 | 8192 | 2048 |
| Dimensions | 1024 / 512 / 256 | 768 down to 64 (MRL) | 768 / 512 / 256 / 128 (MRL) |
| Languages | 100+ | English (model card language tag) | 100+ |
| MTEB | not published as a single number | 62.28 at 768d | Google: top 3 under 1B on MTEB multilingual |
| Licence | AWS service terms | Apache 2.0 | Gemma terms, gated download |
| llama.cpp | no, API only | yes, official GGUF, 15 quantizations | yes, GGUF from ggml-org |
| Cost | $0.02 per 1M tokens plus network egress from the cluster | infra only | infra only |

Also looked at, kept as revisit triggers rather than options for this corpus:

- nomic-embed-text-v2-moe: ~100 languages, MRL 768 to 256, Apache 2.0, official GGUF, but
  context is 512 tokens, too short for whole-file chunks.
- Qwen3-Embedding-0.6B: 100+ languages, 32k context, user-defined 32 to 1024 dims, Apache
  2.0, official GGUF, MTEB multilingual 64.33. Strongest multilingual candidate in this size,
  four times the parameters of nomic v1.5.

## Decision

Use **nomic-ai/nomic-embed-text-v1.5**, GGUF `Q8_0`, served by llama.cpp `llama-server` on
`/v1/embeddings`.

| Parameter | Value |
|---|---|
| Quantization | Q8_0, about 140 MiB on disk |
| Dimensions | 768 stored, MRL truncation to 256 allowed for a shortlist stage |
| Context | 8192 tokens |
| Pooling | mean |
| Task prefixes | `search_document:` for indexed chunks, `search_query:` for queries, required by the model card |

## Rationale

1. It matches the corpus. The corpus is English code and docs, the model is trained for
   English, and the only reason to pay for a multilingual model is a multilingual corpus.
2. It is the smallest option that still gives 8192 context. EmbeddingGemma stops at 2048, which
   forces a chunking strategy before there is a corpus to tune it on. Titan has 8192 too but
   leaves the cluster.
3. MRL is the hedge against the dimension lock-in. If 768 turns out too heavy for Qdrant on
   KinD, truncate to 256 without changing the model or re-embedding with a different one.
4. It keeps the lab self-contained. No API key in the bootstrap path, no egress from KinD, no
   per-token bill, and the same container runs locally, as a sidecar, and behind llm-d.
5. It is what the rest of the course infrastructure assumes. The abox `feat/llmd-embeddings`
   branch already serves this model, so lab 4 compares like with like.

## Consequences

- 768 is now the collection dimension in Qdrant. Changing the model means a full re-embed.
- Every string sent to the model must carry a task prefix. A missing prefix returns a valid
  looking vector that retrieves badly, this is the failure mode to test for first.
- Chunks are bounded by 8192 tokens, code files above that are split.
- English only. The moment the corpus gains meeting transcripts or Slack in Ukrainian, this
  ADR is superseded and Qwen3-Embedding-0.6B is the first candidate to benchmark.

## Benchmark rule

MTEB is a shortlist, not a decision. Before this model serves any agent, run a retrieval
benchmark on this repository: a small set of gold queries against the indexed corpus, track
Recall@5 and MRR, run it in Phoenix so the numbers stay next to the traces. Repeat the same
run at 256 dims to decide whether the shortlist stage is worth keeping. Lab 4 reuses this
benchmark to compare against `all-MiniLM-L6-v2` from the default Qdrant MCP.

## Tokenomics

The course asks to count agent tokens spent on preparing the ADR and executing the TODO.

| Step | Tokens | Notes |
|---|---|---|
| Research and this ADR | to record | Claude Code session, read from the session cost at the end |
| TODO: local llama.cpp run | to record | |
| TODO: sidecar and llm-d in cluster | to record | |

The artifact that should make the next run cheaper is the TODO itself, written as an
agent-executable runbook.

## Sources

- Titan Text Embeddings V2: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-titan-text-embeddings-v2.html
- nomic-embed-text-v1.5: https://huggingface.co/nomic-ai/nomic-embed-text-v1.5 and https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF
- Nomic Embed Matryoshka: https://www.nomic.ai/news/nomic-embed-matryoshka
- nomic-embed-text-v2-moe: https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe
- Qwen3-Embedding-0.6B: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- EmbeddingGemma: https://huggingface.co/google/embeddinggemma-300m
- MTEB leaderboard: https://huggingface.co/spaces/mteb/leaderboard
