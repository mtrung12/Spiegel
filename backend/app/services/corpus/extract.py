"""
Turn a filtered corpus into a theme distribution.

``CorpusFilter`` shrinks a raw harvest from tens of thousands of items to a few
hundred ranked ones, but a few hundred items is still far too much text to hand
to anything downstream. This module takes that ranked list and answers the only
question the pipeline actually needs:

    what does this audience already argue about, and in what proportion?

The output is a distribution - a ranked list of themes, each with a share of the
corpus, a sentiment split and a handful of real quotes.

Two things keep the counts meaningful:

* **Carried vocabulary.** Each batch is shown the tags earlier batches produced
  and told to reuse them verbatim. Without it the model re-words the same
  objection every batch and one recurring complaint splits into several 1x
  entries, which makes the distribution meaningless.
* **A merge pass.** Batches still drift - batch 1 says "price too high", batch 7
  says "too expensive" before it ever sees the first tag. One final LLM call
  over the ~30 surviving *labels* folds synonyms together. Comparing 30 labels
  is two orders of magnitude cheaper than embedding 300 items, and it is better
  at the job: cosine similarity puts "price too high" and "price is fair" close
  together because they are topically near, and merging those would corrupt the
  distribution.

The corpus is category discussion with the campaign's own brand deliberately
excluded (see ``models.SourceQuery.excludes_pattern``), so what comes out is a
**prior** - how this audience already talks about the category - not a
prediction of how they will react to the campaign.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ...config import Config
from ...models.task import TaskCancelled
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from ...utils.openai_chat_compat import is_gpt5_family
from ...utils.pipeline_logger import llm_caller
from .models import CorpusItem

logger = get_logger('spiegel.corpus.extract')

# Short codes keep the model's reply small, so a batch of 25 fits in one
# response even with a theme tag per item. Mirrors ContentSentimentService.
_CODE_TO_SENTIMENT = {
    'pos': 'positive',
    'neu': 'neutral',
    'neg': 'negative',
    'positive': 'positive',
    'neutral': 'neutral',
    'negative': 'negative',
}


def normalize_theme(tag: str) -> str:
    """
    Fold a theme tag to a comparison key.

    Merges "Price too high", "price too high." and "prices too high" onto one
    key. Deliberately conservative: it only merges differently *typed* tags, not
    differently *worded* ones - that is what the merge pass is for.
    """
    key = tag.lower().strip()
    key = re.sub(r'[^a-z0-9\s]', '', key)
    key = re.sub(r'\s+', ' ', key).strip()
    words = []
    for word in key.split():
        if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        words.append(word)
    return ' '.join(words)


# Ceiling on how much one item's engagement can add on top of the item itself.
# A theme's weight is dominated by how many distinct people raised it; upvotes
# only break ties between themes raised equally often.
MAX_ENGAGEMENT_BONUS = 0.5


def engagement_weight(score: int) -> float:
    """
    Damped weight for one item's upvotes, in ``0..MAX_ENGAGEMENT_BONUS``.

    Upvotes are power-law distributed: one viral thread can out-score the rest
    of the corpus combined. Plain log damping is not enough on its own - log1p
    of a 5000-upvote comment is still ~8.5, so a single viral item would
    outweigh several ordinary ones and one loud thread would dictate the
    distribution. Saturating the bonus keeps the count as the primary signal:
    a highly-upvoted item counts as at most one and a half ordinary items.

    The shape is the same saturation BM25 applies to term frequency, for the
    same reason - the tenth upvote says far less than the first.
    """
    damped = math.log1p(max(score, 0))
    return MAX_ENGAGEMENT_BONUS * damped / (damped + 4.0)


@dataclass
class ThemeGroup:
    """One recurring objection or hook, with the items behind it."""

    theme: str
    count: int = 0
    weight: float = 0.0
    positive: int = 0
    neutral: int = 0
    negative: int = 0
    examples: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, item: CorpusItem, sentiment: str) -> None:
        self.count += 1
        self.weight += 1.0 + engagement_weight(item.score)
        if sentiment == 'positive':
            self.positive += 1
        elif sentiment == 'negative':
            self.negative += 1
        else:
            self.neutral += 1

    @property
    def dominant_sentiment(self) -> str:
        """The sentiment most of this theme's items carry."""
        ranked = [
            ('negative', self.negative),
            ('positive', self.positive),
            ('neutral', self.neutral),
        ]
        return max(ranked, key=lambda kv: kv[1])[0]

    def to_dict(self, denominator: int) -> Dict[str, Any]:
        share = round(self.count / denominator * 100, 1) if denominator else 0.0
        return {
            'theme': self.theme,
            'count': self.count,
            'share_pct': share,
            'weight': round(self.weight, 2),
            'sentiment': {
                'positive': self.positive,
                'neutral': self.neutral,
                'negative': self.negative,
            },
            'dominant_sentiment': self.dominant_sentiment,
            'examples': self.examples,
        }


class CorpusThemeExtractor:
    """
    Batched LLM coding pass over a filtered corpus.

    Reads ranked ``CorpusItem`` and emits a theme distribution. The filtering
    that got the corpus down to this size is LLM-free and lives in ``filters``;
    this is the first stage that spends tokens, which is why it runs over the
    top ``max_items`` rather than everything that survived.
    """

    BATCH_SIZE = 25
    CLASSIFY_CHAR_LIMIT = 600
    EXCERPT_CHAR_LIMIT = 300
    MAX_CARRIED_THEMES = 25
    MAX_THEMES = 12
    MAX_THEME_EXAMPLES = 3
    # Themes below this share are long-tail noise for the purposes of shaping a
    # population - a 0.3% theme cannot be represented in an agent pool anyway.
    MIN_SHARE_PCT = 2.0

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        max_items: int = 300,
    ):
        self._llm = llm_client
        self.max_items = max_items

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

    # ── Classification ──────────────────────────────────────────

    def _classify_batch(
        self,
        batch: List[CorpusItem],
        topic_context: str,
        known_themes: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """
        Label one batch. Returns ``{uid: "sentiment|tag"}``.

        A failed batch yields nothing rather than raising - losing 25 items out
        of 300 skews the distribution slightly; losing the whole harvest to one
        bad response loses the build.
        """
        lines = []
        for idx, item in enumerate(batch):
            text = item.full_text[:self.CLASSIFY_CHAR_LIMIT].replace('\n', ' ')
            lines.append(f'[{idx}] ({item.source}/{item.kind}) {text}')
        numbered = '\n'.join(lines)

        vocabulary = ""
        if known_themes:
            listed = '\n'.join(f'- {t}' for t in known_themes)
            vocabulary = (
                "\n## Theme tags already in use\n"
                "Reuse one of these VERBATIM whenever it fits. Only invent a new "
                "tag when none of them describes the item.\n"
                f"{listed}\n"
            )

        prompt = f"""{topic_context}{vocabulary}

Below are {len(batch)} real posts and comments from public discussion about this
product category. They were written by members of the target audience before any
campaign ran. Classify each one.

{numbered}

For every item return:
- "s": the writer's sentiment ABOUT THE CATEGORY OR THE PRODUCTS IN IT - "pos",
  "neu" or "neg". Judge the attitude expressed, not whether the wording is
  polite. Sarcasm counts as negative. An item that only reports a fact, asks a
  neutral question, or is off-topic is "neu".
- "tag": the single dominant theme, 2-4 words, lower case.
  For a negative item this is the OBJECTION ("price too high", "portions too
  small", "shipping too slow").
  For a positive item this is the DRAW - what they like ("saves weeknight time",
  "ingredient quality", "value for money").
  For a neutral item give the topic.
  Reuse the exact same wording whenever two items share a theme, so they can be
  counted together.

Return JSON only:
{{"items": [{{"i": 0, "s": "neg", "tag": "price too high"}}]}}

Every index from 0 to {len(batch) - 1} must appear exactly once."""

        # A GPT-5 model spends part of its budget on reasoning tokens before it
        # writes anything, so the cap that fits 25 labels on a classic chat
        # model can leave it with no room for the answer.
        max_tokens = 8192 if is_gpt5_family(Config.LLM_MODEL_NAME) else 2048

        try:
            with llm_caller('CorpusThemeExtractor', f"batch of {len(batch)}"):
                result = self.llm.chat_json(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a market research analyst coding open-ended survey "
                                "responses. Return pure JSON. Be consistent: identical themes "
                                "must get identical tags."
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
                "corpus theme batch failed, skipping it: %s: %s",
                type(e).__name__, str(e)[:120],
            )
            return {}

        labels: Dict[str, str] = {}
        for entry in result.get("items", []) or []:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("i"))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(batch):
                continue

            code = str(entry.get("s", "neu")).strip().lower()
            sentiment = _CODE_TO_SENTIMENT.get(code, 'neutral')
            tag = str(entry.get("tag", "") or "").strip()[:60]
            if not tag:
                continue
            labels[batch[idx].uid] = f"{sentiment}|{tag}"
        return labels

    def _classify(
        self,
        items: Sequence[CorpusItem],
        topic_context: str,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, str]:
        """Label every item, one batch at a time, carrying the tag vocabulary."""
        labels: Dict[str, str] = {}
        theme_counts: Counter = Counter()
        total_batches = (len(items) + self.BATCH_SIZE - 1) // self.BATCH_SIZE

        for batch_idx in range(total_batches):
            # A dozen sequential LLM calls is the longest stretch of this pass,
            # so a stop request is honoured between batches rather than after.
            if should_cancel and should_cancel():
                raise TaskCancelled("corpus classification cancelled")

            start = batch_idx * self.BATCH_SIZE
            batch = list(items[start:start + self.BATCH_SIZE])
            logger.info(
                "classifying corpus batch %d/%d (%d items)",
                batch_idx + 1, total_batches, len(batch),
            )

            carried = [t for t, _ in theme_counts.most_common(self.MAX_CARRIED_THEMES)]
            batch_labels = self._classify_batch(batch, topic_context, carried)
            labels.update(batch_labels)

            for value in batch_labels.values():
                theme_counts[value.split('|', 1)[1]] += 1

        return labels

    # ── Merge pass ──────────────────────────────────────────────

    def _merge_synonyms(self, tags: List[str]) -> Dict[str, str]:
        """
        Fold synonymous tags onto one canonical label.

        Operates on the ~30 surviving labels, not on the items, so this is one
        small call regardless of corpus size. Returns ``{tag: canonical}``;
        tags absent from the mapping keep their own wording.
        """
        if len(tags) < 2:
            return {}

        listed = '\n'.join(f'- {t}' for t in tags)
        prompt = f"""These theme tags were produced independently while coding public
discussion about one product category. Some describe the SAME underlying theme in
different words.

{listed}

Group only the tags that mean the same thing. Two tags about the same subject but
with opposite attitudes ("price too high" vs "value for money") are DIFFERENT
themes and must NOT be grouped. When in doubt, leave a tag on its own.

For each group pick the clearest wording as the canonical label.

Return JSON only, listing only the tags that belong to a group of two or more:
{{"groups": [{{"canonical": "price too high", "members": ["too expensive", "price too high"]}}]}}"""

        max_tokens = 4096 if is_gpt5_family(Config.LLM_MODEL_NAME) else 1024

        try:
            with llm_caller('CorpusThemeExtractor', f"merging {len(tags)} tags"):
                result = self.llm.chat_json(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You consolidate survey coding frames. Return pure JSON. "
                                "Never merge themes that carry opposite sentiment."
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
                "corpus theme merge failed, keeping unmerged tags: %s: %s",
                type(e).__name__, str(e)[:120],
            )
            return {}

        known = {t: t for t in tags}
        mapping: Dict[str, str] = {}
        for group in result.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            canonical = str(group.get("canonical", "") or "").strip()
            members = group.get("members") or []
            if not canonical or not isinstance(members, list):
                continue
            for member in members:
                member = str(member or "").strip()
                # Only remap tags the model was actually shown; a hallucinated
                # member would otherwise invent a theme with no items behind it.
                if member and member in known:
                    mapping[member] = canonical
        return mapping

    # ── Aggregation ─────────────────────────────────────────────

    def _excerpt(self, item: CorpusItem, sentiment: str) -> Dict[str, Any]:
        """A quote card: enough to read, not the whole thread."""
        text = item.full_text
        return {
            'uid': item.uid,
            'source': item.source,
            'kind': item.kind,
            'text': text[:self.EXCERPT_CHAR_LIMIT],
            'truncated': len(text) > self.EXCERPT_CHAR_LIMIT,
            'score': item.score,
            'sentiment': sentiment,
        }

    def extract(
        self,
        items: Sequence[CorpusItem],
        topic_context: str = "",
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """
        Build the theme distribution for one harvest.

        Args:
            items: Filtered, ranked corpus items - the output of ``CorpusFilter``
            topic_context: Short description of the product category, so the
                model codes sentiment about the right subject
            should_cancel: Optional predicate polled between batches

        Returns:
            A distribution dict, with an empty theme list when there was nothing
            to classify.

        Raises:
            TaskCancelled: the caller asked for the pipeline to stop
        """
        # The list arrives ranked by quality*0.4 + relevance*0.6, so the head is
        # the most useful material rather than an arbitrary slice.
        selected = list(items[:self.max_items])
        empty = {
            'n_items': 0,
            'n_classified': 0,
            'model': Config.LLM_MODEL_NAME,
            'themes': [],
            'sentiment': {'positive': 0, 'neutral': 0, 'negative': 0},
        }
        if not selected:
            return empty

        labels = self._classify(selected, topic_context, should_cancel)
        if not labels:
            logger.warning("corpus theme extraction classified nothing")
            return {**empty, 'n_items': len(selected)}

        raw_tags = sorted({value.split('|', 1)[1] for value in labels.values()})
        merge_map = self._merge_synonyms(raw_tags)

        groups: Dict[str, ThemeGroup] = {}
        display_counts: Dict[str, Counter] = {}
        overall = Counter()

        by_uid = {item.uid: item for item in selected}
        for uid, value in labels.items():
            item = by_uid.get(uid)
            if item is None:
                continue
            sentiment, tag = value.split('|', 1)
            overall[sentiment] += 1

            canonical = merge_map.get(tag, tag)
            key = normalize_theme(canonical)
            if not key:
                continue

            group = groups.setdefault(key, ThemeGroup(theme=canonical))
            group.add(item, sentiment)
            display_counts.setdefault(key, Counter())[canonical] += 1
            group.examples.append(self._excerpt(item, sentiment))

        n_classified = sum(overall.values())
        ranked = sorted(groups.values(), key=lambda g: (g.weight, g.count), reverse=True)

        out: List[Dict[str, Any]] = []
        for group in ranked[:self.MAX_THEMES]:
            share = (group.count / n_classified * 100) if n_classified else 0.0
            if share < self.MIN_SHARE_PCT:
                continue
            key = normalize_theme(group.theme)
            variants = display_counts.get(key)
            if variants:
                group.theme = variants.most_common(1)[0][0]
            # Loudest quotes first, so the examples are the ones that landed.
            group.examples.sort(key=lambda e: e['score'], reverse=True)
            group.examples = group.examples[:self.MAX_THEME_EXAMPLES]
            out.append(group.to_dict(n_classified))

        return {
            'n_items': len(selected),
            'n_classified': n_classified,
            'model': Config.LLM_MODEL_NAME,
            'themes': out,
            'sentiment': {
                'positive': overall.get('positive', 0),
                'neutral': overall.get('neutral', 0),
                'negative': overall.get('negative', 0),
            },
        }


def render_distribution(distribution: Optional[Dict[str, Any]], max_themes: int = 8) -> str:
    """
    Render a distribution as a prompt block.

    Used both for the graph episode text and for the LLM prompts that shape
    agents, so a theme always reads the same way wherever it turns up.
    """
    if not distribution or not distribution.get('themes'):
        return ""

    total = distribution.get('n_classified') or 0
    lines = [
        "## Audience priors from public discussion",
        f"Coded from {total} real posts and comments about this product category, "
        "gathered before the campaign ran. These are existing attitudes, not "
        "reactions to the campaign.",
        "",
    ]
    for theme in distribution['themes'][:max_themes]:
        lines.append(
            f"- {theme['theme']} - {theme['share_pct']}% of discussion, "
            f"mostly {theme['dominant_sentiment']}"
        )
        for example in theme.get('examples', [])[:1]:
            quote = example['text'].replace('\n', ' ').strip()
            lines.append(f'    e.g. "{quote}"')
    return '\n'.join(lines)
