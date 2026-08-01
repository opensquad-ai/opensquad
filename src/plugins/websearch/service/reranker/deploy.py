"""Qwen3-Reranker-0.6B HTTP sidecar for websearch relevance ranking.

Started automatically by websearch ``service/main.py``. Exposes:
  POST /rerank  {queries, documents} -> {scores}  (P(yes) per pair)
  GET  /health
"""

from __future__ import annotations

import asyncio
import os
import sys

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoTokenizer

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SNAPSHOT = os.path.join(
    _HERE,
    "models",
    "models--Qwen--Qwen3-Reranker-0.6B",
    "snapshots",
    "e61197ed45024b0ed8a2d74b80b4d909f1255473",
)
MODEL_PATH = os.environ.get("WEBSEARCH_RERANKER_MODEL_PATH", _DEFAULT_SNAPSHOT).strip() or _DEFAULT_SNAPSHOT
PORT = int(os.environ.get("WEBSEARCH_RERANKER_PORT", "8111") or "8111")
HOST = os.environ.get("WEBSEARCH_RERANKER_HOST", "127.0.0.1").strip() or "127.0.0.1"

app = FastAPI(title="Qwen3-Reranker-0.6B API")
tokenizer = None
model = None
YES_ID = None
NO_ID = None

# Official Qwen3-Reranker prompt template. The model is a causal LM that judges
# relevance by predicting "yes"/"no"; the relevance score = P(yes).
PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based "
    "on the Query and the Instruct provided. Note that the answer can only be "
    '"yes" or "no".<|im_end|>\n<|im_start|>user\n'
)
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"


class RerankRequest(BaseModel):
    # One query per document, aligned by index, so each result is scored against
    # the query that actually found it (supports multi-query SERP merges).
    queries: list[str]
    documents: list[str]


class RerankResponse(BaseModel):
    scores: list[float]


@app.on_event("startup")
async def load_model():
    global tokenizer, model, YES_ID, NO_ID
    if not os.path.isdir(MODEL_PATH):
        raise RuntimeError(
            f"Reranker model path not found: {MODEL_PATH}. "
            "Set WEBSEARCH_RERANKER_MODEL_PATH or place weights under service/reranker/models/."
        )
    print(f"[WebSearch Reranker] Loading Qwen3-Reranker-0.6B from {MODEL_PATH} ...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, padding_side="left")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, torch_dtype=dtype).to(device).eval()
    YES_ID = tokenizer("yes", add_special_tokens=False).input_ids[0]
    NO_ID = tokenizer("no", add_special_tokens=False).input_ids[0]
    print(f"[WebSearch Reranker] Model loaded on {device} (yes={YES_ID}, no={NO_ID}).")


def _score_sync(queries: list[str], documents: list[str]) -> list[float]:
    texts = [
        PREFIX + f"<Instruct>: {INSTRUCT}\n<Query>: {q}\n<Document>: {d}" + SUFFIX
        for q, d in zip(queries, documents, strict=True)
    ]
    enc = tokenizer(texts, return_tensors="pt", padding=True, truncation=True, max_length=1024)
    enc = {k: v.to(model.device) for k, v in enc.items()}
    with torch.no_grad():
        logits = model(**enc).logits[:, -1, :]
    # Left-padded -> last position is the real last token; P(yes) over {no, yes}.
    probs = torch.softmax(logits[:, [NO_ID, YES_ID]], dim=-1)[:, 1]
    return probs.float().cpu().tolist()


@app.post("/rerank")
async def rerank(req: RerankRequest):
    if model is None:
        raise HTTPException(503, "Model not loaded")
    if len(req.queries) != len(req.documents):
        raise HTTPException(400, "queries and documents must have equal length")
    if not req.documents:
        return RerankResponse(scores=[])
    try:
        scores = await asyncio.to_thread(_score_sync, req.queries, req.documents)
        return RerankResponse(scores=scores)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "model_loaded": model is not None}


if __name__ == "__main__":
    if sys.platform == "win32":
        for _stream in (sys.stdout, sys.stderr):
            if hasattr(_stream, "reconfigure"):
                _stream.reconfigure(encoding="utf-8", errors="replace")
    print(f"[WebSearch Reranker] Starting on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT)
