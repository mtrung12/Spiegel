"""
Derive a corpus query from a campaign brief.

The harvester needs three things the brief never states outright: what category
terms to search, what brand wording to keep *out*, and where to look. One LLM
call reads the brief and answers all three.

``exclude_terms`` is the part that matters. It is the contamination guard - it
keeps the campaign's own brand and slogan out of a corpus that is supposed to
describe the category, so the simulation predicts a reaction instead of reading
one that already happened. A missed brand name is a quiet failure: the harvest
still succeeds, it just leaks the answer. So every proper noun the brief presents
as the advertiser's own is worth excluding, and over-excluding costs little.
"""

import re
from dataclasses import dataclass
from typing import Any, List, Optional

from ...config import Config
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from ...utils.openai_chat_compat import is_gpt5_family
from ...utils.pipeline_logger import llm_caller
from .models import SourceQuery

logger = get_logger('spiegel.corpus.query_builder')

# How much of the brief the model reads. The category and the brand are always
# established early; the rest is schedule and budget detail that costs tokens
# without changing the answer.
BRIEF_CHAR_LIMIT = 12000

MAX_TERMS = 8
MAX_EXCLUDES = 12
MAX_SUBREDDITS = 8

# Subreddit names are used to build URLs, so anything that is not a plain
# subreddit token is dropped rather than escaped.
_SUBREDDIT_RE = re.compile(r'^[A-Za-z0-9_]{2,21}$')


@dataclass
class DerivedQuery:
    """What to harvest, plus the one-line category description for later prompts."""

    query: SourceQuery
    context: str = ""


def _clean_list(values: Any, limit: int) -> List[str]:
    """Coerce an LLM list field to a bounded list of non-empty strings."""
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or '').strip()
        if not text or len(text) > 80:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _clean_subreddits(values: Any) -> List[str]:
    """Keep only well-formed subreddit names, stripping any r/ prefix."""
    cleaned: List[str] = []
    for value in _clean_list(values, MAX_SUBREDDITS * 2):
        name = value.strip().lstrip('/')
        if name.lower().startswith('r/'):
            name = name[2:]
        if _SUBREDDIT_RE.match(name):
            cleaned.append(name)
        if len(cleaned) >= MAX_SUBREDDITS:
            break
    return cleaned


def derive_query(
    brief_text: str,
    llm_client: Optional[LLMClient] = None,
    max_items: int = 800,
    since_days: int = 365,
) -> Optional[DerivedQuery]:
    """
    Ask the model what to search for.

    Args:
        brief_text: The campaign brief, as extracted from the uploaded files
        llm_client: Injected client, for tests
        max_items: Fetch budget, split across the enabled sources
        since_days: How far back to look

    Returns:
        A DerivedQuery, or None when the brief yields no usable terms - the
        caller treats that as "skip the harvest", not as an error.
    """
    if not (brief_text or '').strip():
        return None

    client = llm_client or LLMClient()
    excerpt = brief_text[:BRIEF_CHAR_LIMIT]

    prompt = f"""Below is a marketing campaign brief.

---
{excerpt}
---

We want to gather public discussion about the PRODUCT CATEGORY this campaign
competes in, so we can learn how the audience already talks about it. We must
NOT gather discussion about the advertiser's own brand - that would leak the
answer we are trying to predict.

Return:
- "terms": {MAX_TERMS} or fewer search phrases describing the product category,
  the competitors, and the problems the product solves. Use the words real
  people would use, not marketing language. No brand name from this brief.
- "exclude_terms": every name, sub-brand, product name and slogan that belongs
  to the ADVERTISER in this brief. Be thorough - a missed name contaminates the
  corpus. Include the company name even when the campaign is for one product.
- "subreddits": up to {MAX_SUBREDDITS} subreddit names where this category is
  discussed, without the "r/" prefix. Omit any you are not confident exists.
- "context": one sentence naming the product category, for later prompts.

Return JSON only:
{{"terms": ["meal kit delivery"], "exclude_terms": ["BrandName"],
  "subreddits": ["cooking"], "context": "Meal kit subscription boxes."}}"""

    max_tokens = 4096 if is_gpt5_family(Config.LLM_MODEL_NAME) else 1024

    try:
        with llm_caller('CorpusQueryBuilder', 'campaign brief'):
            result = client.chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You plan market research. Return pure JSON. Never put the "
                            "advertiser's own brand into the search terms."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                max_attempts=2,
            )
    except Exception as e:
        logger.warning(
            "corpus query derivation failed, skipping the harvest: %s: %s",
            type(e).__name__, str(e)[:120],
        )
        return None

    terms = _clean_list(result.get('terms'), MAX_TERMS)
    if not terms:
        logger.info("corpus query derivation produced no terms; skipping the harvest")
        return None

    excludes = _clean_list(result.get('exclude_terms'), MAX_EXCLUDES)

    # A term that is itself excluded would search for exactly what the guard is
    # meant to keep out, so the exclusion wins.
    exclude_keys = {e.lower() for e in excludes}
    terms = [t for t in terms if t.lower() not in exclude_keys]
    if not terms:
        logger.info("every derived term was also excluded; skipping the harvest")
        return None

    query = SourceQuery(
        terms=terms,
        exclude_terms=excludes,
        subreddits=_clean_subreddits(result.get('subreddits')),
        max_items=max_items,
        since_days=since_days,
    )
    logger.info(
        "derived corpus query: %d terms, %d excludes, %d subreddits",
        len(query.terms), len(query.exclude_terms), len(query.subreddits),
    )
    return DerivedQuery(
        query=query,
        context=str(result.get('context') or '').strip()[:300],
    )
