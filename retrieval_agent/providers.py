from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "3072"))  # must match the DB vector(N) column


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    pg_sslmode: str
    endpoint: str
    api_key: str
    api_version: str
    embedding_deployment: str
    chat_deployment: str
    min_vector_score: float
    min_bm25_score: float
    rrf_constant: int
    temporal_boost_days: int
    temporal_boost_magnitude: float

    @classmethod
    def load(cls) -> Settings:
        settings = cls(
            pg_host=os.getenv("AZURE_PG_HOST") or os.getenv("PG_HOST", ""),
            pg_port=int(os.getenv("AZURE_PG_PORT") or os.getenv("PG_PORT", "5432")),
            pg_database=os.getenv("AZURE_PG_DATABASE") or os.getenv("PG_DATABASE", ""),
            pg_user=os.getenv("AZURE_PG_USER") or os.getenv("PG_USER", ""),
            pg_password=os.getenv("AZURE_PG_PASSWORD") or os.getenv("PG_PASSWORD", ""),
            pg_sslmode=os.getenv("AZURE_PG_SSLMODE") or os.getenv("PG_SSLMODE", "require"),
            endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", ""),
            embedding_deployment=os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT") or os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", ""),
            chat_deployment=(
                os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
                or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")
            ),
            min_vector_score=float(os.getenv("RETRIEVAL_MIN_SCORE", "0.68")),
            min_bm25_score=float(os.getenv("RETRIEVAL_MIN_BM25_SCORE", "1.0")),
            rrf_constant=int(os.getenv("RETRIEVAL_RRF_CONSTANT", "60")),
            temporal_boost_days=int(os.getenv("RETRIEVAL_TEMPORAL_BOOST_DAYS", "365")),
            temporal_boost_magnitude=float(os.getenv("RETRIEVAL_TEMPORAL_BOOST_MAGNITUDE", "0.02")),
        )
        if not 0 <= settings.min_vector_score <= 1:
            raise ValueError("RETRIEVAL_MIN_SCORE must be between 0 and 1")
        if settings.min_bm25_score < 0:
            raise ValueError("RETRIEVAL_MIN_BM25_SCORE must not be negative")
        if settings.rrf_constant < 1:
            raise ValueError("RETRIEVAL_RRF_CONSTANT must be a positive integer")
        if settings.temporal_boost_days < 1:
            raise ValueError("RETRIEVAL_TEMPORAL_BOOST_DAYS must be a positive integer")
        if not -0.1 <= settings.temporal_boost_magnitude <= 0.1:
            raise ValueError("RETRIEVAL_TEMPORAL_BOOST_MAGNITUDE must be between -0.1 and 0.1")
        return settings

    def database_conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo

        missing = [
            name for name, value in (
                ("AZURE_PG_HOST", self.pg_host),
                ("AZURE_PG_DATABASE", self.pg_database),
                ("AZURE_PG_USER", self.pg_user),
                ("AZURE_PG_PASSWORD", self.pg_password),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Missing database configuration: " + ", ".join(missing))
        return make_conninfo(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.pg_database,
            user=self.pg_user,
            password=self.pg_password,
            sslmode=self.pg_sslmode,
            connect_timeout=10,
        )

    def openai_url(self, deployment: str, operation: str) -> str:
        missing = [
            name for name, value in (
                ("AZURE_OPENAI_ENDPOINT", self.endpoint),
                ("AZURE_OPENAI_API_VERSION", self.api_version),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Missing Azure OpenAI configuration: " + ", ".join(missing))
        return (
            f"{self.endpoint}/openai/deployments/{quote(deployment, safe='')}/"
            f"{operation}?api-version={quote(self.api_version, safe='')}"
        )


def _post_json(settings: Settings, url: str, body: dict) -> dict:
    if not settings.api_key:
        raise RuntimeError("Missing AZURE_OPENAI_API_KEY")
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"api-key": settings.api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            try:
                payload = json.loads(response.read())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Azure OpenAI returned an invalid JSON response") from exc
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Azure OpenAI returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach Azure OpenAI: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Azure OpenAI returned an unexpected response")
    return payload


def embed_texts(settings: Settings, texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if not settings.embedding_deployment:
        raise RuntimeError("Missing AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    payload = _post_json(
        settings,
        settings.openai_url(settings.embedding_deployment, "embeddings"),
        {"input": texts, "dimensions": EMBEDDING_DIMENSIONS},
    )
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError("Embedding provider returned an unexpected vector count")
    try:
        data.sort(key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("Embedding provider returned malformed vector data") from exc
    result = []
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
            raise RuntimeError(f"Embedding provider must return exactly {EMBEDDING_DIMENSIONS} dimensions")
        if any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in vector):
            raise RuntimeError("Embedding provider returned non-finite numeric values")
        result.append([float(value) for value in vector])
    return result


def complete_json(settings: Settings, system_prompt: str, user_prompt: str) -> dict:
    deployment = settings.chat_deployment or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "")
    if not deployment:
        raise RuntimeError("Missing AZURE_OPENAI_CHAT_DEPLOYMENT")
    payload = _post_json(
        settings,
        settings.openai_url(deployment, "chat/completions"),
        {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 2500,
            "response_format": {"type": "json_object"},
        },
    )
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("Chat deployment returned no choices")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Chat deployment returned no JSON content")
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Chat deployment returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Chat deployment JSON must be an object")
    return result
