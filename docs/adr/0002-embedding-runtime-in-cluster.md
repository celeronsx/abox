# ADR-0002: Where the embedding model runs in the cluster

- Status: Accepted, every option below was deployed and measured on this cluster on 2026-09-16
- Date: 2026-09-16
- Related: [ADR-0001](./0001-text-embedding-model.md), TODO `docs/todo/embeddings-cluster-sidecar-llmd.md`

## Context

ADR-0001 picks nomic-embed-text-v1.5 served by llama.cpp. This ADR decides how that
process is scheduled inside abox. Lab 3 names two shapes to cover, sidecar and llm-d. The
upstream branch `feat/llmd-embeddings` ships a third, a plain Deployment, and calls it the
shipped route. So three shapes:

1. Shared Deployment plus Service, one process, many consumers.
2. Sidecar, the embedder runs inside the consumer pod as a native sidecar (init container
   with `restartPolicy: Always`).
3. llm-d, the modelservice chart running llama.cpp behind an InferencePool and an endpoint
   picker (EPP), model mounted as an image volume.

Two facts about this cluster that upstream does not have to deal with:

| Fact | Consequence |
|---|---|
| Nodes are arm64 (KinD on a MacBook), the published image `ghcr.io/den-vasyliev/abox/nomic-embed:v1.18.1-4ccc0ff` is a single amd64 manifest | It runs only under qemu emulation. Measured below, it is unusable. |
| Kubernetes v1.35, containerd 2.2, `ImageVolume` gate reports `BETA 1` on the API server | llm-d's image volume works here, kind v1.37 is not required as the upstream comment suggests. |

## Measurements

Same model, same `--ctx-size 16384 --ubatch-size 2048 --parallel 8`, same requests: a
162-token document, 7 sequential calls after warmup, from a pod in the cluster. Memory is
container working set from `crictl stats`, taken after the run.

| Shape | Node arch | Start to Ready | p50 doc | query | Working set | Extra cost | Vectors |
|---|---|---|---|---|---|---|---|
| Deployment, `llama.cpp:server-b10920` multi-arch, `-hf` Q8_0 | arm64 native | 40 s (146 MB from HF) | 155 ms | 87 ms | 134 MiB idle, 170 MiB after load | none | reference |
| Deployment, baked image `nomic-embed:v1.18.1-4ccc0ff` | amd64 under qemu | 16 s (model in image) | **24 113 ms** | 2 289 ms | 86 MiB | 794 CPU-seconds for 9 calls | differ from native, cos 0.7053 vs 0.7038 |
| llm-d decode, same server via image volume | arm64 native | 49 s (image pull) | 137 ms | 27 ms | 129 MiB idle, 164 MiB after load | EPP pod 83 MiB and 35 CPU-seconds while idle, plus 2 HelmReleases, 1 HelmRepository, 1 OCIRepository, inference CRDs | identical to native, first 8 components equal |
| Sidecar, same server as init container | arm64 native | 15 s (image cached) | 247 ms for 366 tokens over localhost | not measured | 210 MiB embedder, 13 MiB client | one embedder per consumer pod | same weights, same vectors |

Three things the numbers say that the upstream comments do not:

- The published baked image is not an option on arm64. 155 times slower than native, and the
  vectors move, so anything embedded through it would not even match the native index.
- llm-d and the plain Deployment run the same binary on the same weights and produce identical
  vectors. The 137 vs 155 ms gap is two pods on two nodes, not a property of llm-d. At one
  replica the EPP has nothing to pick and costs 83 MiB and CPU for it.
- The sidecar working set is higher than the shared one (210 vs 170 MiB) because each pod
  maps its own copy of the GGUF. Two consumers means two copies.

## Decision

1. **Default serving path is a shared Deployment plus Service** in namespace `llama-cpp`,
   image `ghcr.io/ggml-org/llama.cpp:server-b10920` (multi-arch), model pulled with
   `-hf nomic-ai/nomic-embed-text-v1.5-GGUF:Q8_0`. Manifest in
   `releases/llama-cpp-embeddings.yaml`. In-cluster URL
   `http://llama-cpp-embeddings.llama-cpp:8090/v1/embeddings`.
2. **Sidecar is for ingestion and evaluation Jobs only.** Native sidecar, `--host 0.0.0.0`
   even though the client is on localhost, because kubelet probes reach the pod IP, not
   loopback. Not a serving path.
3. **llm-d is deployed for comparison, not as the default.** It stays in the cluster for lab 4
   because that lab compares retrieval routes, and it becomes the default only when a second
   model or a second replica appears. Until then it is 83 MiB and four Flux objects for no
   routing decision.

## Rationale

- Native arm64 is the only thing that runs at usable speed here, so the multi-arch upstream
  llama.cpp image wins over the baked one. Pulling the GGUF at start costs 40 s and needs
  egress from the nodes, which this cluster has. Baking a multi-arch image is the next step if
  that egress ever goes away, `images/nomic-embed/Dockerfile` on the upstream branch already
  builds `linux/amd64,linux/arm64`.
- Shared beats sidecar for serving on every axis that matters at this scale: one copy of the
  weights, one thing to upgrade, one endpoint for agentgateway to route to.
- Sidecar keeps one property the shared path cannot: the Job that re-indexes carries the exact
  embedder it was benchmarked with. Mixing vectors from two model builds in one Qdrant
  collection is a silent correctness bug, the amd64 row above is the proof.
- llm-d is not rejected. Prefill and decode disaggregation and KV-cache routing do nothing for
  an encoder that has neither, so the only value left is replica scheduling, and there is one
  replica.

## Consequences

- Consumers point at `llama-cpp-embeddings.llama-cpp:8090`. Qdrant collection `abox-nomic`
  is bound to vectors from this route. Switching to llm-d or a baked image later is a re-embed,
  not a repoint, even though the measured vectors are identical today.
- `releases/llama-cpp-embeddings.yaml` in this fork diverges from upstream on purpose,
  different image and args. Merging upstream changes to that file needs a look, not a fast
  forward.
- The cluster now carries the inference-extension CRDs and the llm-d objects from the upstream
  branch. They were applied by hand for the measurement and are not in this fork's `releases/`
  yet. Lab 4 decides whether they move in.
- Revisit triggers: a generative model added to the cluster, a second embedding replica,
  nodes without egress to Hugging Face, or an amd64 cluster where the baked image runs natively.
