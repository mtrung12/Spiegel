"""
Vector index over what the simulated audience actually wrote.

The retrieval tools in ``zep_tools`` search the knowledge graph: entities,
relationships and LLM-extracted facts. That is a *summary* layer. The raw posts
and comments the agents authored never reach the chatbot verbatim, so a question
like "what did people say about the price?" is answered from second-hand facts
instead of from the text itself.

This module indexes that text in Qdrant and gives the chatbot semantic recall
over it.

Chunking
--------
Posts and comments are already atomic utterances - a tweet or a Reddit comment,
usually one to five sentences. Running a document splitter over them would cut
single thoughts in half and gain nothing, so the unit of indexing is **one
record, one point**. No splitting.

The real retrieval problem here is not length, it is that a comment is
meaningless on its own: "Totally agree, this is a scam" has no topic, no
referent and no brand name, so it matches no query. Every record is therefore
embedded with a short context header naming the platform, the author, and - for
a comment - the post it replies to. The raw text is kept separately in the
payload so what gets shown is still the audience's own words.

A long-form Reddit post is the only case that is genuinely too big for one
embedding. Those (rare) outliers fall back to the sentence-boundary splitter
already used for uploaded documents.
"""

import uuid
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from ..config import Config
from ..utils.file_parser import split_text_into_chunks
from ..utils.logger import get_logger
from .content_sentiment import ContentItem, ContentSentimentService

logger = get_logger('mirofish.content_index')


# Above this many characters a single record is too long for one embedding and
# is split. Roughly 500 tokens - comfortably inside every embedding model's
# window, and far above the length of an ordinary post or comment.
MAX_EMBED_CHARS = 2000
SPLIT_CHUNK_SIZE = 1500
SPLIT_OVERLAP = 150

# Parent post text quoted into a comment's context header. Enough to carry the
# topic, short enough that the comment itself still dominates the embedding.
PARENT_CONTEXT_CHARS = 200

EMBED_BATCH_SIZE = 64

COLLECTION_NAME = "simulation_content"

# Deterministic point IDs, so re-indexing upserts in place instead of duplicating.
_POINT_NAMESPACE = uuid.UUID("6f0a1c2e-7b1d-4a55-9c3e-2b8f4d5a1e77")


class ContentIndexError(RuntimeError):
    """Indexing or search could not be completed."""


# ═══════════════════════════════════════════════════════════════
# Text preparation
# ═══════════════════════════════════════════════════════════════

def build_embedding_text(item: ContentItem, parent: Optional[ContentItem] = None) -> str:
    """
    The text that actually gets embedded: a context header plus the record.

    The header is what makes a short reaction retrievable. Without it a comment
    carries neither the topic it is reacting to nor the channel it appeared on,
    and no realistic query will retrieve it.
    """
    if item.kind == "comment" and parent is not None:
        quoted = parent.content[:PARENT_CONTEXT_CHARS].replace('\n', ' ').strip()
        if len(parent.content) > PARENT_CONTEXT_CHARS:
            quoted += "..."
        header = (
            f'[{item.platform} comment by {item.author_name}, '
            f'replying to {parent.author_name}: "{quoted}"]'
        )
    elif item.kind == "comment":
        header = f'[{item.platform} comment by {item.author_name}]'
    else:
        header = f'[{item.platform} post by {item.author_name}]'

    return f"{header}\n{item.content}"


def _records(
    items: List[ContentItem],
    parents: Dict[Tuple[str, int], ContentItem],
    simulation_id: str,
    is_seed: bool = False,
) -> List[Dict[str, Any]]:
    """Turn content items into the points to upsert, one per item (see module docstring)."""
    records: List[Dict[str, Any]] = []

    for item in items:
        parent = None
        if item.kind == "comment" and item.post_id is not None:
            parent = parents.get((item.platform, item.post_id))

        text = build_embedding_text(item, parent)

        # One record, one point - except for the rare long-form post.
        pieces = (
            [text] if len(text) <= MAX_EMBED_CHARS
            else split_text_into_chunks(text, SPLIT_CHUNK_SIZE, SPLIT_OVERLAP)
        )

        for index, piece in enumerate(pieces):
            point_id = str(uuid.uuid5(_POINT_NAMESPACE, f"{simulation_id}:{item.uid}:{index}"))
            records.append({
                "id": point_id,
                "text": piece,
                "payload": {
                    "simulation_id": simulation_id,
                    "uid": item.uid,
                    "platform": item.platform,
                    "kind": item.kind,
                    "author_name": item.author_name,
                    "author_id": item.author_id,
                    "post_id": item.post_id,
                    "created_at": item.created_at,
                    "likes": item.likes,
                    "dislikes": item.dislikes,
                    "is_seed": is_seed,
                    # The audience's own words, unmodified - this is what gets
                    # quoted back. ``text`` above is the retrieval surface only.
                    "content": item.content,
                    "parent_content": parent.content if parent else None,
                    "parent_author": parent.author_name if parent else None,
                    "part": index,
                },
            })

    return records


# ═══════════════════════════════════════════════════════════════
# The index
# ═══════════════════════════════════════════════════════════════

class ContentIndexService:
    """Qdrant-backed semantic search over one simulation's posts and comments."""

    def __init__(self, client: Any = None, embedder: Any = None):
        self._client = client
        self._embedder = embedder
        self._vector_size: Optional[int] = None

    # ── Backends ────────────────────────────────────────────────

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                from qdrant_client import QdrantClient
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise ContentIndexError(
                    "qdrant-client is not installed; run pip install -r backend/requirements.txt"
                ) from exc

            if Config.QDRANT_URL:
                self._client = QdrantClient(
                    url=Config.QDRANT_URL,
                    api_key=Config.QDRANT_API_KEY or None,
                )
            else:
                # ponytail: embedded local storage, no server to run. It takes an
                # exclusive file lock, so it is single-process only - set
                # QDRANT_URL to point at a real server when running several
                # workers or when the index outgrows one machine.
                self._client = QdrantClient(path=Config.QDRANT_PATH)
        return self._client

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            if not Config.EMBEDDING_API_KEY:
                raise ContentIndexError(
                    f"EMBEDDING_API_KEY is not configured (base_url={Config.EMBEDDING_BASE_URL})"
                )
            self._embedder = OpenAI(
                api_key=Config.EMBEDDING_API_KEY,
                base_url=Config.EMBEDDING_BASE_URL,
            )
        return self._embedder

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed texts in batches through the OpenAI-format embeddings endpoint."""
        vectors: List[List[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[start:start + EMBED_BATCH_SIZE]
            response = self.embedder.embeddings.create(
                model=Config.EMBEDDING_MODEL_NAME,
                input=batch,
            )
            # The API may return the batch out of order; index is authoritative.
            ordered = sorted(response.data, key=lambda d: d.index)
            vectors.extend(d.embedding for d in ordered)
        return vectors

    # ── Collection ──────────────────────────────────────────────

    def _ensure_collection(self, vector_size: int) -> None:
        from qdrant_client import models

        if self.client.collection_exists(COLLECTION_NAME):
            return

        self.client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            ),
        )
        # One collection holds every simulation, so the tenant field must be
        # indexed or each filtered search degrades into a full scan. Embedded
        # Qdrant ignores payload indexes (and warns), so only ask a server.
        if Config.QDRANT_URL:
            self.client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name="simulation_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        logger.info(f"created Qdrant collection {COLLECTION_NAME} (dim={vector_size})")

    def _sim_filter(self, simulation_id: str, **equals: Any) -> Any:
        from qdrant_client import models

        conditions = [
            models.FieldCondition(
                key="simulation_id",
                match=models.MatchValue(value=simulation_id),
            )
        ]
        for key, value in equals.items():
            if value is not None:
                conditions.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=value))
                )
        return models.Filter(must=conditions)

    def indexed_count(self, simulation_id: str) -> int:
        """Points already stored for this simulation (0 when nothing is indexed)."""
        try:
            if not self.client.collection_exists(COLLECTION_NAME):
                return 0
            result = self.client.count(
                collection_name=COLLECTION_NAME,
                count_filter=self._sim_filter(simulation_id),
                exact=True,
            )
            return result.count
        except Exception as e:
            logger.warning(f"could not count indexed content for {simulation_id}: {e}")
            return 0

    # ── Indexing ────────────────────────────────────────────────

    def index_simulation(self, simulation_id: str, force: bool = False) -> int:
        """
        Index every post and comment of one simulation. Returns points written.

        Point IDs are deterministic, so this is idempotent: re-running after the
        feed grows upserts the existing records and adds the new ones.
        """
        from qdrant_client import models

        # Reads the same SQLite feed the sentiment digest reads, seed posts
        # (the campaign's own creative) separated from audience reactions.
        authored, seeds = ContentSentimentService._collect(simulation_id)

        # Parent lookup, so a comment can be embedded with the post it replies to.
        parents: Dict[Tuple[str, int], ContentItem] = {
            (item.platform, item.item_id): item
            for item in list(authored) + list(seeds)
            if item.kind == "post"
        }

        records = (
            _records(authored, parents, simulation_id)
            + _records(seeds, parents, simulation_id, is_seed=True)
        )

        if not records:
            logger.info(f"no content to index for simulation {simulation_id}")
            return 0

        if not force and self.indexed_count(simulation_id) >= len(records):
            logger.info(f"simulation {simulation_id} already indexed ({len(records)} records)")
            return 0

        vectors = self.embed([r["text"] for r in records])
        if not vectors:
            raise ContentIndexError("embedding endpoint returned no vectors")

        self._ensure_collection(len(vectors[0]))
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                models.PointStruct(id=r["id"], vector=v, payload=r["payload"])
                for r, v in zip(records, vectors)
            ],
        )

        logger.info(f"indexed {len(records)} content records for simulation {simulation_id}")
        return len(records)

    # ── Search ──────────────────────────────────────────────────

    def search(
        self,
        simulation_id: str,
        query: str,
        limit: int = 10,
        kind: Optional[str] = None,
        platform: Optional[str] = None,
        auto_index: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Semantic search over what the audience wrote.

        Args:
            simulation_id: Which simulation to search
            query: Natural-language query
            limit: Number of results
            kind: Restrict to "post" or "comment"
            platform: Restrict to "twitter" or "reddit"
            auto_index: Build the index on first use if it is empty

        Returns:
            Result dicts: content, author_name, platform, kind, likes, score, ...
        """
        if not query or not query.strip():
            return []

        if auto_index and self.indexed_count(simulation_id) == 0:
            self.index_simulation(simulation_id)

        if not self.client.collection_exists(COLLECTION_NAME):
            return []

        vector = self.embed([query])[0]
        response = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=vector,
            query_filter=self._sim_filter(simulation_id, kind=kind, platform=platform),
            limit=limit,
            with_payload=True,
        )

        results = []
        for point in response.points:
            payload = dict(point.payload or {})
            payload["score"] = point.score
            results.append(payload)
        return results

    def search_as_text(
        self,
        simulation_id: str,
        query: str,
        limit: int = 10,
        kind: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> str:
        """Search and format the hits for an LLM prompt."""
        try:
            results = self.search(simulation_id, query, limit, kind, platform)
        except ContentIndexError as e:
            return f"Content search unavailable: {e}"

        if not results:
            return f"No audience content found for: {query}"

        lines = [f"Audience content matching '{query}' ({len(results)} results):", ""]
        for i, r in enumerate(results, 1):
            tag = "CAMPAIGN CREATIVE" if r.get("is_seed") else r.get("kind", "post")
            lines.append(
                f"{i}. [{r.get('platform')}/{tag}] {r.get('author_name')} "
                f"(+{r.get('likes', 0)}/-{r.get('dislikes', 0)})"
            )
            if r.get("parent_content"):
                parent = r["parent_content"][:PARENT_CONTEXT_CHARS].replace('\n', ' ')
                lines.append(f"   replying to {r.get('parent_author')}: \"{parent}\"")
            lines.append(f"   \"{r.get('content', '')}\"")
            lines.append("")

        return "\n".join(lines)
