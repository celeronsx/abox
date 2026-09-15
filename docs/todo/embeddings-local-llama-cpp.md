# TODO: run nomic-embed-text-v1.5 locally with llama.cpp

Implements [ADR-0001](../adr/0001-text-embedding-model.md). Agent-executable. Every command
below was run on 2026-09-16 on a MacBook (arm64, Docker VM 6 CPU / 16 GiB) and the outputs
quoted are real.

Goal: a callable OpenAI-compatible `/v1/embeddings` endpoint on `localhost:8088` that returns
768-dim vectors, accepts inputs up to 8192 tokens, and passes a semantic check.

Done means section 5 passes. "The container is healthy" is not done.

## 1. Prerequisites

- Docker running. No sudo needed, nothing is installed on the host.
- `curl`, `python3`.
- About 150 MiB free for the model cache.
- Port 8088 free.

## 2. Start the server

```bash
mkdir -p "$HOME/.cache/llama.cpp"

docker run -d --name abox-embeddings -p 8088:8080 \
  -v "$HOME/.cache/llama.cpp:/root/.cache/llama.cpp" \
  ghcr.io/ggml-org/llama.cpp:server \
    -hf nomic-ai/nomic-embed-text-v1.5-GGUF:Q8_0 \
    --embeddings --pooling mean \
    -c 8192 -b 8192 -ub 8192 \
    --rope-scaling yarn --rope-freq-scale 0.25 \
    --host 0.0.0.0 --port 8080
```

| Flag | Why |
|---|---|
| `-hf ...:Q8_0` | Pulls the 146 MB Q8_0 GGUF from Hugging Face into the mounted cache. Q8_0 per ADR-0001. |
| `--embeddings` | Embedding-only mode. Without it `/v1/embeddings` is not served. |
| `--pooling mean` | nomic v1.5 is a BERT-style encoder trained with mean pooling. `last` is for decoder embedders and returns wrong vectors silently. |
| `-c 8192 -b 8192 -ub 8192` | Full context, batch raised to match so one 8k input is not split. |
| `--rope-scaling yarn --rope-freq-scale 0.25` | Unlocks 8192. Not the `0.75` from the model card, see section 3. |

First start downloads the model, the image is arm64 and amd64.

## 3. Check the context actually is 8192

```bash
docker logs abox-embeddings 2>&1 | grep -E "n_ctx_slot|capping"
```

Expected:

```
srv  load_model: initializing, n_slots = 4, n_ctx_slot = 8192
```

Why `0.25` and not the `0.75` the model card shows. The GGUF reports `n_ctx_train = 2048`.
`llama-server` caps a slot at `n_ctx_train / rope_freq_scale`. With `0.75` you ask for 8192
and get 2730, and the server tells you so:

```
W srv  load_model: the slot context (8192) exceeds the training context of the model (2730) - capping
I srv  load_model: initializing, n_slots = 4, n_ctx_slot = 2730
```

The card's example is for the standalone `embedding` binary. `2048 / 0.25 = 8192`, so `0.25`
is the value that gives the full window. If you see `capping` in the log, the value is wrong.

## 4. Health and shape

```bash
curl -sf localhost:8088/health && echo ok
curl -s localhost:8088/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"input":"search_query: how does Flux reconcile releases from OCI?"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['data'][0]['embedding']), d['usage'])"
```

Expected: `768 {'prompt_tokens': 15, 'total_tokens': 15}`.

Every input must start with a task prefix, `search_document: ` for indexed text and
`search_query: ` for questions. The model card marks this as required. Without it the server
still returns 768 numbers, they just retrieve badly.

## 5. Acceptance: semantic check and long input

```bash
python3 - <<'EOF'
import json, urllib.request, math
def emb(t):
    r = urllib.request.Request("http://localhost:8088/v1/embeddings",
        data=json.dumps({"input": t}).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))["data"][0]["embedding"]
def cos(a, b):
    return sum(x*y for x, y in zip(a, b)) / (math.sqrt(sum(x*x for x in a)) * math.sqrt(sum(y*y for y in b)))
def trunc(v, n):
    v = v[:n]; s = math.sqrt(sum(x*x for x in v)); return [x/s for x in v]

q  = emb("search_query: how does Flux pull the releases artifact from the OCI registry?")
d1 = emb("search_document: Flux Operator creates a ResourceSetInputProvider that polls oci://ghcr.io/den-vasyliev/abox/releases and reconciles the Kustomizations from that OCI artifact.")
d2 = emb("search_document: To make borscht, simmer beetroot, cabbage and potatoes with dill and serve with sour cream.")
print(f"768d  related={cos(q,d1):.3f}  unrelated={cos(q,d2):.3f}  pass={cos(q,d1) > cos(q,d2)}")
print(f"256d  related={cos(trunc(q,256),trunc(d1,256)):.3f}  unrelated={cos(trunc(q,256),trunc(d2,256)):.3f}  pass={cos(trunc(q,256),trunc(d1,256)) > cos(trunc(q,256),trunc(d2,256))}")

long = "search_document: " + " ".join(["kubernetes flux gitops agentgateway kagent qdrant phoenix reconcile"] * 450)
r = urllib.request.Request("http://localhost:8088/v1/embeddings",
    data=json.dumps({"input": long}).encode(), headers={"Content-Type": "application/json"})
d = json.load(urllib.request.urlopen(r))
print("long input ok, prompt_tokens =", d["usage"]["prompt_tokens"])
EOF
```

Result on 2026-09-16:

```
768d  related=0.732  unrelated=0.396  pass=True
256d  related=0.731  unrelated=0.387  pass=True
long input ok, prompt_tokens = 7656
```

Three things this proves that a liveness check does not:

- The related document outscores the unrelated one, so pooling and prefixes are right.
- Truncating to 256 dims and re-normalizing keeps the same ordering with almost the same
  margin, so the MRL shortlist stage from ADR-0001 is viable.
- A 7656-token input is accepted, which is only possible if section 3 is correct.

## 6. Stop and clean

```bash
docker rm -f abox-embeddings
```

The model stays in `~/.cache/llama.cpp`, the next start does not download again.

## 7. Definition of done

- [ ] `docker logs` shows `n_ctx_slot = 8192` and no `capping`
- [ ] `/v1/embeddings` returns 768 dims
- [ ] section 5 prints `pass=True` twice and accepts the long input
- [ ] tokens spent by the agent on this TODO recorded in ADR-0001, Tokenomics
