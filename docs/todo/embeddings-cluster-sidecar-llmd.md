# TODO: run the embedding model in the cluster, shared, sidecar, llm-d

Implements [ADR-0002](../adr/0002-embedding-runtime-in-cluster.md). Agent-executable. Every
command was run on 2026-09-16 against the abox KinD cluster (3 arm64 nodes, Kubernetes
v1.35.0, containerd 2.2). Numbers quoted are from that run.

Done means section 6 passes for the shared route. Sidecar and llm-d are verified once each.

## 1. Prerequisites

- abox cluster up, `kubectl get kustomizations -A` all `True`.
- Nodes can reach `huggingface.co` and `ghcr.io`. On KinD on a Mac they can. On Codespaces run
  `make fix-egress` first.
- `docker` on the host, only to read container stats from the kind nodes (`crictl` lives there).

## 2. Shared Deployment, the default

Manifest: `releases/llama-cpp-embeddings.yaml`. Namespace `llama-cpp`, one replica, Service
on 8090, HTTPRoute `/llamacpp` on `agentgateway-external`.

```bash
kubectl apply -f releases/llama-cpp-embeddings.yaml
kubectl rollout status -n llama-cpp deploy/llama-cpp-embeddings --timeout=180s
```

Why these args, and why not upstream's baked image:

| Choice | Why |
|---|---|
| `ghcr.io/ggml-org/llama.cpp:server-b10920` | Multi-arch (amd64, arm64, s390x), same build llm-d uses upstream. |
| `-hf nomic-ai/nomic-embed-text-v1.5-GGUF:Q8_0` | Pulls the 146 MB GGUF at start. The upstream baked image is amd64 only and ran 155 times slower under emulation here, p50 24 s. |
| `--ctx-size 16384 --ubatch-size 2048 --parallel 8` | Upstream's shape, 8 slots of 2048. Kept so lab 4 compares like with like. |
| requests `500m / 512Mi`, limits `2 / 1Gi` | Measured working set 134 MiB idle, 170 MiB under load. |

Expected: pod Ready in about 40 s, most of it the download.

## 3. Sidecar, for Jobs only

Native sidecar, KEP-753, GA since Kubernetes 1.29. The embedder is an `initContainers` entry
with `restartPolicy: Always`, so it starts before the main container and lives as long as
the pod.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: embed-ingest
  namespace: llama-cpp
spec:
  restartPolicy: Never
  initContainers:
    - name: embedder
      image: ghcr.io/ggml-org/llama.cpp:server-b10920
      restartPolicy: Always
      args: ["--host","0.0.0.0","--port","8090","--embeddings",
             "--ctx-size","16384","--ubatch-size","2048","--parallel","8",
             "-hf","nomic-ai/nomic-embed-text-v1.5-GGUF:Q8_0"]
      startupProbe:
        httpGet: {path: /health, port: 8090}
        periodSeconds: 5
        failureThreshold: 36
      resources:
        requests: {cpu: 500m, memory: 512Mi}
        limits: {cpu: "2", memory: 1Gi}
  containers:
    - name: ingest
      image: python:3.12-alpine
      command: ["python3", "your-ingest-script.py"]   # talks to http://127.0.0.1:8090
```

The one mistake that costs three minutes: `--host 127.0.0.1` looks right for a sidecar the
client reaches over localhost, and the pod sits in `Init:0/1` forever. kubelet runs the
startup probe against the pod IP, `Get "http://10.244.2.26:8090/health": connection refused`,
kills the init container, restarts it, repeats. Bind `0.0.0.0`.

Measured with a 5-call ingest client: Ready in 15 s with the image cached, 366-token
documents at 247 ms average over localhost, embedder working set 210 MiB, client 13 MiB.

## 4. llm-d, for comparison

Objects come from the upstream branch `feat/llmd-embeddings`, not from this fork's
`releases/`. Two applies, because the inference CRDs arrive through a Flux GitRepository and
are not there yet when the first apply reaches `InferenceObjective`.

```bash
B=upstream/feat/llmd-embeddings
git show "${B}:releases/crds/inference-extension-crds.yaml" | kubectl apply -f -
git show "${B}:releases/llmd.yaml" | kubectl apply -f -
# first apply ends with: no matches for kind "InferenceObjective" ... ensure CRDs are installed first
kubectl wait -n flux-system kustomization/inference-extension-crds --for=condition=Ready --timeout=120s
git show "${B}:releases/llmd.yaml" | kubectl apply -f -
kubectl get helmrelease -n llm-d
```

What comes up: `llm-d-embedding` (modelservice chart v0.3.17, llama.cpp container, model from
image volume), `llm-d-pool` (inferencepool chart 1.5.0, the EPP), an InferencePool, an
InferenceObjective and an HTTPRoute `/llmd`. Decode pod Ready in 49 s, EPP in 7 s.

ImageVolume is what mounts the model. Check the gate before assuming it works elsewhere:

```bash
kubectl get --raw /metrics | grep 'kubernetes_feature_enabled{name="ImageVolume"'
# kubernetes_feature_enabled{name="ImageVolume",stage="BETA"} 1
```

## 5. Measure

Working set and CPU per container, from a kind node, no metrics-server needed:

```bash
docker exec abox-worker crictl stats -o json | python3 -c '
import json,sys
for s in json.load(sys.stdin)["stats"]:
    n=s["attributes"]["metadata"]["name"]
    if "memory" in s: print(n, int(s["memory"]["workingSetBytes"]["value"])//2**20, "MiB", int(s["cpu"]["usageCoreNanoSeconds"]["value"])//10**9, "cpu-s")'
```

Latency from inside the cluster, one throwaway pod per backend so a slow one does not block
the others:

```bash
kubectl run bench --rm -i --restart=Never -n llama-cpp --image=python:3.12-alpine -- python3 -c '
import json,urllib.request,time,statistics
u="http://llama-cpp-embeddings.llama-cpp:8090/v1/embeddings"
doc="search_document: "+" ".join(["Flux Operator reconciles the releases Kustomization from an OCI artifact."]*6)
def call(t):
    r=urllib.request.Request(u,data=json.dumps({"input":t}).encode(),headers={"Content-Type":"application/json"})
    s=time.perf_counter(); d=json.load(urllib.request.urlopen(r,timeout=90)); return (time.perf_counter()-s)*1000,d
call("search_query: warmup"); lat=sorted(call(doc)[0] for _ in range(7))
print("p50", round(statistics.median(lat)), "ms  min", round(lat[0]), "ms")'
```

Result on 2026-09-16, 162-token document: shared 155 ms p50, llm-d 137 ms, sidecar 247 ms for
366 tokens. Same query and document vectors from shared and llm-d matched to the printed
precision.

## 6. Definition of done

- [ ] `kubectl get pods -n llama-cpp` shows `llama-cpp-embeddings` Ready, `uname -m` inside
      the pod matches the node, no `qemu` in the picture
- [ ] `/v1/embeddings` on the Service returns 768 dims for a prefixed input
- [ ] p50 for a ~160-token document is under 300 ms from inside the cluster
- [ ] sidecar pod reaches `2/2 Running` with `--host 0.0.0.0`
- [ ] llm-d `helmrelease` both `True`, decode pod `uname -m` matches the node
- [ ] tokens spent by the agent on this TODO recorded in ADR-0001, Tokenomics
