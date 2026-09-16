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
