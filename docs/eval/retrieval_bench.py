"""Direct retrieval benchmark, no LLM: embed each gold query with the same model as the collection,
search Qdrant, report rank of the expected document. nomic via llama.cpp on localhost:8088,
MiniLM via fastembed locally. Qdrant via port-forward on localhost:6333."""
import json, urllib.request, pathlib, math
HERE=pathlib.Path(__file__).parent; GOLD=json.loads((HERE/"gold.json").read_text())
def http(url, body):
    r=urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type":"application/json"}); return json.load(urllib.request.urlopen(r, timeout=120))
def nomic(q): return http("http://localhost:8088/v1/embeddings", {"input":"search_query: "+q})["data"][0]["embedding"]
from fastembed import TextEmbedding
_m=TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
def minilm(q): return list(_m.query_embed(q))[0].tolist()
def search(coll, vec, limit=8, using=None):
    body={"query":vec,"limit":limit,"with_payload":["name","kind","metadata"]}
    if using: body["using"]=using
    res=http(f"http://localhost:6333/collections/{coll}/points/query", body)
    out=[]
    for p in res["result"]["points"]:
        pl=p["payload"]; name=pl.get("name") or (pl.get("metadata") or {}).get("name"); out.append((name, round(p["score"],3)))
    return out
def run(label, coll, emb, using=None):
    print(f"\n### {label}"); rr=[]; r1=r3=0
    for g in GOLD:
        hits=search(coll, emb(g["q"]), using=using)
        names=[]; [names.append(n) for n,_ in hits if n not in names]   # dedupe chunks
        ranks=[names.index(d)+1 for d in g["expect_docs"] if d in names]
        best=min(ranks) if ranks else None
        rr.append(1/best if best else 0); r1+= (best==1); r3+= (best is not None and best<=3)
        print(f"{g['id']} expect={g['expect_docs']} best_rank={best} top3={names[:3]} top1_score={hits[0][1] if hits else None}")
    n=len([g for g in GOLD if g.get("expect_docs")]); print(f"== {label}: Recall@1={r1}/{n} Recall@3={r3}/{n} MRR={sum(rr)/n:.3f}")
run("nomic-768 / abox-nomic (Denys Go MCP, chunked)", "abox-nomic", nomic)
run("minilm-384 / abox-minilm (official mcp-server-qdrant)", "abox-minilm", minilm, using="fast-all-minilm-l6-v2")
