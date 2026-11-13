"""
Stage 2: Structuring Agent
(diagram box: "Converts normalized text into structured SOP/runbook format
and generates vector embeddings for similarity search.")

Responsibilities:
  1. Render every NormalizedRecord into a consistent SOP/runbook-style
     markdown block, regardless of which of the 4 sources it came from.
     This is what makes a ticket resolution and a formal RCA "look the
     same" to a downstream retrieval agent.
  2. Generate a vector embedding per record so records can be retrieved by
     semantic similarity rather than exact keyword match.

Embedding model: Azure OpenAI `text-embedding-3-large` (3072 dimensions),
via the same `langchain_openai.AzureOpenAIEmbeddings` client and env vars
(AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_VERSION /
AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME) used by the Underwriting Solution
codebase (see Jarvis/MCP/Document_Query_MCP/query_retriever_pgvector.py).
Since it's a pretrained model (not fit on our corpus like the earlier
TF-IDF version), there is no vectorizer to persist -- embed_query() just
calls the same client as fit_embeddings().
"""

from __future__ import annotations

import os

from langchain_openai import AzureOpenAIEmbeddings

from schemas import NormalizedRecord

EMBEDDING_DIM = 3072

_client: AzureOpenAIEmbeddings | None = None


def _get_embeddings_client() -> AzureOpenAIEmbeddings:
    """Lazily build the Azure OpenAI embeddings client from env vars, and
    reuse it across calls. Raises RuntimeError with a clear message if the
    required env vars aren't set, instead of a confusing error from deep
    inside the Azure SDK."""
    global _client
    if _client is not None:
        return _client

    required = [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing Azure OpenAI env var(s) for embeddings: "
            f"{', '.join(missing)}. Fill these in in .env (same Azure "
            "OpenAI resource/deployment as the Underwriting Solution project)."
        )

    _client = AzureOpenAIEmbeddings(
        azure_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME"),
        openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        chunk_size=1024,
    )
    return _client


SOP_TEMPLATE = """## {title}

**Source:** {source_type} ({source_ref_id})
**Module:** {module} | **Category:** {category} | **Severity:** {severity}
**Resolution Type:** {resolution_type}
**Date:** {event_date}{version_line}

### Summary / Symptom
{summary}

### Resolution
{resolution}
"""


def _render_sop(record: NormalizedRecord) -> str:
    version_line = f"\n**Guidewire Version:** {record.guidewire_version}" if record.guidewire_version else ""
    # raw_text already concatenates the meaningful narrative fields per
    # source type (see ingestion.py); split it heuristically into a
    # "what happened" vs "what fixed it" view for the SOP template.
    text = record.raw_text
    midpoint = len(text) // 2
    summary_part = text[: max(midpoint, 120)].strip()
    resolution_part = text[max(midpoint, 120):].strip() or text.strip()

    return SOP_TEMPLATE.format(
        title=record.title,
        source_type=record.source_type.value,
        source_ref_id=record.source_ref_id,
        module=record.module.value,
        category=record.category.value,
        severity=record.severity.value,
        resolution_type=record.resolution_type.value,
        event_date=record.event_date.isoformat(),
        version_line=version_line,
        summary=summary_part,
        resolution=resolution_part,
    )


def render_sops(records: list[NormalizedRecord]) -> list[NormalizedRecord]:
    """Render structured SOP text for every record. Does NOT touch
    embeddings -- this is the half of the Structuring Agent that can run
    as its own step, independent of when embeddings get generated."""
    for r in records:
        r.structured_sop = _render_sop(r)
    return records


def fit_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate one embedding per text, in the same order, via Azure
    OpenAI. (Kept the name `fit_embeddings` for compatibility with
    pipeline.py / step4_store_embeddings.py, but there's no "fitting"
    step with a pretrained model -- it's just a batched embed call.)

    This is the half of the Structuring Agent that can run as its own
    step, independent of ingestion/tagging -- e.g. reading `raw_text`
    straight back out of Postgres for rows that don't have an embedding
    yet (see step4_store_embeddings.py)."""
    client = _get_embeddings_client()
    return client.embed_documents(texts)


def structure_and_embed(records: list[NormalizedRecord]) -> list[NormalizedRecord]:
    """Convenience wrapper used by the one-shot pipeline.py: render SOPs
    AND generate embeddings in one call. Equivalent to running
    render_sops() then fit_embeddings() yourself (as step3/step4 do)."""
    render_sops(records)
    corpus = [f"{r.title} {r.raw_text}" for r in records]
    embeddings = fit_embeddings(corpus)
    for r, emb in zip(records, embeddings):
        r.embedding = emb
    return records


def embed_query(text: str) -> list[float]:
    """Embed a free-text query using the same Azure OpenAI model used on
    the corpus, so results are comparable via cosine similarity."""
    client = _get_embeddings_client()
    return client.embed_query(text)


if __name__ == "__main__":
    from ingestion import ingest_all

    records = ingest_all()
    records = structure_and_embed(records)
    print(f"Structured {len(records)} records. Embedding dimension: {len(records[0].embedding)}")
    print("\n--- Example structured SOP ---\n")
    print(records[0].structured_sop)
