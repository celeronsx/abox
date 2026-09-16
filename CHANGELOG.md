# Changelog

## [Unreleased]

### Added

- ADR-0001, text embedding model ([docs/adr/0001-text-embedding-model.md](docs/adr/0001-text-embedding-model.md)).
  nomic-embed-text-v1.5 as Q8_0 GGUF, 768 dims with MRL down to 256, 8192 context, mean pooling.
  Three candidates compared per the course method, cloud (Titan V2), mid self-hosted (nomic),
  small MRL (EmbeddingGemma). Corpus is English code and docs. Revisit trigger: a Ukrainian
  corpus, then Qwen3-Embedding-0.6B goes first to the benchmark.
- TODO, local run with llama.cpp ([docs/todo/embeddings-local-llama-cpp.md](docs/todo/embeddings-local-llama-cpp.md)).
  Docker `llama-server` on 8088. The model card's `--rope-freq-scale 0.75` caps the slot at
  2730 tokens on `llama-server`, `0.25` gives 8192. Acceptance is a cosine check, 0.732 related
  vs 0.396 unrelated, same ordering at 256 dims, 7656-token input accepted.
- ADR-0002, where the model runs in the cluster ([docs/adr/0002-embedding-runtime-in-cluster.md](docs/adr/0002-embedding-runtime-in-cluster.md)).
  Shared Deployment as default, sidecar for Jobs, llm-d kept for comparison. All three deployed
  and measured on the arm64 KinD cluster. The upstream baked image is amd64 only and ran at
  p50 24 s under emulation against 155 ms native, so the fork uses the multi-arch
  `llama.cpp:server-b10920` with `-hf` instead.
- TODO, cluster run ([docs/todo/embeddings-cluster-sidecar-llmd.md](docs/todo/embeddings-cluster-sidecar-llmd.md)).
  Shared, sidecar and llm-d step by step with the two pitfalls hit on the way, sidecar bound to
  127.0.0.1 fails its probe, and `InferenceObjective` needs a second apply after the CRD
  Kustomization is Ready.
- `releases/llama-cpp-embeddings.yaml`, the shared embedding Deployment, Service and
  `/llamacpp` HTTPRoute for this fork. Diverges from upstream on image and args by decision
  of ADR-0002.
- ADR-0003, agentic retrieval evaluation ([docs/adr/0003-agentic-retrieval-evaluation.md](docs/adr/0003-agentic-retrieval-evaluation.md)).
  Same 8 kagent objects indexed through the upstream Go Qdrant MCP (nomic-768 via llama.cpp) and
  the official mcp-server-qdrant (all-MiniLM-L6-v2, 384). Direct retrieval: nomic Recall@3 8/8,
  MRR 0.854 against MiniLM 7/8, 0.830. Agent level: the nomic agent scored 0/8 because the Go
  server returns `structuredContent: {"body": ""}` and kagent shows the model that instead of the
  text. nomic stays the retrieval path, the fix is an upstream bug report.
- TODO, agentic retrieval eval ([docs/todo/agentic-retrieval-eval.md](docs/todo/agentic-retrieval-eval.md)).
  Corpus export, one-document-per-message ingest, direct Qdrant benchmark, agent-level eval, and
  the checks that tell a serialisation bug from a retrieval miss. Scripts and results in `docs/eval/`.
- `releases/` now carries the lab 4 objects from upstream `feat/llmd-embeddings` (neo4j, mcp-servers,
  agent-retrieval, llmd, inference-extension CRDs) plus `qdrant-mcp-official` in `mcp-servers.yaml`
  and `agent-retrieval-official.yaml`. Both retrieval agents ship with `stream: false`.

### Fixed

- kmcp stdio adapter crash-loop on kind, `fs.inotify.max_user_instances` 128 is not enough with
  Flux, kagent and three MCP adapters on the same kernel. Documented in the TODO, raised to 8192 on
  the nodes.
