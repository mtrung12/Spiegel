"""
Lightweight corpus filters.

Nothing here calls an LLM or loads a model - every check is a regex or a
counter over the raw string. That matters because this stage runs over the
entire fetched corpus (often 10k-100k items) and its whole job is to shrink
that corpus before anything expensive touches it.

Four stages, cheapest first:

1. ``hard drops``   - boilerplate, empty, pure emoji, spam, bot output
2. ``quality``      - statistical signals (entropy, stopword ratio, repetition)
3. ``relevance``    - IDF-weighted overlap with the topic terms, with reply
                      inheritance so short on-topic replies survive
4. ``dedupe``       - 64-bit SimHash with banded lookup, O(n) in practice
"""

import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from math import log, sqrt
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import CorpusItem, KIND_COMMENT

# --------------------------------------------------------------------------
# Lexicons
# --------------------------------------------------------------------------

# Deliberately small. A larger list buys almost nothing here because the ratio,
# not the exact membership, is what carries the signal.
EN_STOPWORDS = frozenset("""
a about after all also am an and any are as at be because been before being but
by can could did do does doing don down during each few for from further had has
have having he her here hers him his how i if in into is it its just me more most
my no nor not now of off on once only or other our out over own same she should
so some such than that the their them then there these they this those through to
too under until up very was we were what when where which while who whom why will
with would you your
""".split())

# Discourse markers, hedges, interjections and near-empty nouns. These are not
# stopwords - they are grammatically contentful but carry no information about
# a product. A comment made entirely of these is fluent, well-formed English
# that says nothing, which no entropy or diversity measure will catch.
FILLER_WORDS = frozenset("""
actually anyway anymore basically dunno guess honestly idk imo imho kinda literally
maybe mean means meant nah nope obviously ok okay probably really seriously sorta
stuff supposedly sure tbh thing things truly whatever yeah yep yup
uh um umm hmm huh ah oh eh meh wow woah oof welp
dont doesnt didnt cant wont im ive id youre theyre thats isnt arent
anybody anyone anything everybody everyone everything nobody somebody someone
something somewhat somehow somewhere anywhere everywhere
bit lot lots kind sort way ways much many quite pretty rather
good bad nice cool great awesome terrible awful amazing fine decent
know knew think thought feel felt seem seems seemed
""".split())

# Comments that are pure social noise. Matched against the whole normalized
# string, so "this" is dropped but "this broke after two weeks" is not.
NOISE_EXACT = frozenset("""
this that same lol lmao lmfao rofl haha hahaha xd yes no yep nope yeah yup nah ok
okay k thanks thank ty thx np nice cool wow omg damn bruh bro fr frfr facts based
cringe w l rip oof yikes same here me too agreed agree true real preach exactly
first second underrated overrated bump deleted removed n a na none nothing idk imo
tldr edit update thanks for sharing thanks for the info great post great video
nice post nice video good video good post love this love it i love this so true
came here to say this take my money shut up and take my money who else who is here
anyone else still watching in first comment sub to my channel follow me back
""".strip().split('\n')) | {
    'this', 'same', 'agreed', 'facts', '+1', '^this', 'came here to say this',
    'underrated comment', 'shut up and take my money',
}

# Multi-word junk that varies too much for exact matching.
NOISE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r'^\s*(first|second|third)!*\s*$',
        r"^\s*who('?s| is| else is)?\s+(here|watching|listening).{0,30}$",
        r'^\s*anyone\s+else\b.{0,25}$',
        r'^\s*(like|comment|share)\s+if\b',
        r'\b(sub(scribe)?\s+to\s+my|check\s+out\s+my)\s+(channel|page|profile)\b',
        r'^\s*(rip|f)\s*$',
        r'^\s*\d{4}\s*\?*\s*$',                       # "2026?"
        r'^\s*(edit|update)\s*\d*\s*:\s*$',
    )
]

# Promotional / scam patterns. These are the highest-precision drops.
SPAM_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r'\b(t\.me/|telegram|whats\s?app)\s*[:+@]?\s*\+?\d',
        r'\bdm\s+me\b.{0,40}\b(for|to)\b',
        r'\b(link|links)\s+in\s+(bio|description)\b',
        r'\b(crypto|forex|binary\s+option|bitcoin)\b.{0,40}\b(profit|invest|expert|recover)\b',
        r'\b(hack|recover)\w*\b.{0,30}\b(account|wallet|funds)\b',
        r'\b(promo|discount)\s*code\b.{0,20}\b(use|apply)\b',
        r'https?://\S+\s+https?://\S+\s+https?://\S+',  # link dumps
    )
]

# Platform boilerplate and bot output.
BOILERPLATE_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r'^\s*\[(deleted|removed)\]\s*$',
        r'\bi\s+am\s+a\s+bot\b',
        r'\bbeep\s+boop\b',
        r'\bthis\s+(action|comment)\s+was\s+performed\s+automatically\b',
        r'\byour\s+(post|submission|comment)\s+(was|has been)\s+removed\b',
        r'\bplease\s+contact\s+the\s+moderators\b',
        r'^\s*>\s*!?.*!<\s*$',                        # spoiler-only comment
    )
]

URL_RE = re.compile(r'https?://\S+|www\.\S+')
MENTION_RE = re.compile(r'[@u]/\w+|@\w+|/?r/\w+')
WHITESPACE_RE = re.compile(r'\s+')
WORD_RE = re.compile(r"[a-z0-9']+")
CJK_RE = re.compile(r'[一-鿿぀-ヿ가-힯]')
EMOJI_RE = re.compile(
    '[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]'
)
REPEAT_RUN_RE = re.compile(r'(.)\1{4,}')


# --------------------------------------------------------------------------
# Signals
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Collapse whitespace and strip. Everything downstream assumes this ran."""
    return WHITESPACE_RE.sub(' ', (text or '')).strip()


def tokenize(text: str) -> List[str]:
    """
    Cheap tokenizer.

    Latin text splits on word characters. CJK has no spaces, so each character
    becomes a token plus its bigram, which is enough for overlap scoring.
    """
    lowered = text.lower()
    tokens = WORD_RE.findall(lowered)
    cjk_chars = CJK_RE.findall(lowered)
    if cjk_chars:
        tokens.extend(cjk_chars)
        tokens.extend(
            cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)
        )
    return tokens


def text_signals(text: str) -> Dict[str, Any]:
    """
    Compute the statistical fingerprint of one string.

    All ratios are 0..1. Cost is a handful of linear passes - roughly 10us for
    a typical comment, so a 100k-item corpus profiles in about a second.
    """
    stripped = normalize(text)
    n_chars = len(stripped)
    if n_chars == 0:
        return {
            'n_chars': 0, 'n_words': 0, 'alpha_ratio': 0.0, 'ttr': 0.0,
            'stopword_ratio': 0.0, 'compression_ratio': 1.0, 'max_char_run': 0,
            'emoji_ratio': 0.0, 'caps_ratio': 0.0, 'digit_ratio': 0.0,
            'punct_ratio': 0.0, 'url_count': 0, 'mention_count': 0,
            'top_token_ratio': 0.0, 'cjk_ratio': 0.0, 'bigram_ttr': 1.0,
            'content_word_count': 0, 'content_word_ratio': 0.0,
        }

    url_count = len(URL_RE.findall(stripped))
    mention_count = len(MENTION_RE.findall(stripped))

    # Score the text with URLs and mentions removed - a comment that is only a
    # link has no content even though the raw string is long.
    content = MENTION_RE.sub(' ', URL_RE.sub(' ', stripped)).strip()
    tokens = tokenize(content)
    n_words = len(tokens)

    alpha_chars = sum(1 for c in content if c.isalpha())
    digit_chars = sum(1 for c in content if c.isdigit())
    upper_chars = sum(1 for c in content if c.isupper())
    punct_chars = sum(1 for c in content if not c.isalnum() and not c.isspace())
    cjk_chars = len(CJK_RE.findall(content))
    emoji_chars = len(EMOJI_RE.findall(stripped))

    counts = Counter(tokens)
    top_token_ratio = (counts.most_common(1)[0][1] / n_words) if n_words else 0.0
    ttr = (len(counts) / n_words) if n_words else 0.0

    # Unique bigrams over total bigrams. Natural prose sits near 1.0 because
    # people rarely repeat a two-word sequence; keyword stuffing and copy-paste
    # padding collapse it well below 0.6. Single-token repetition already shows
    # up in ttr, so this is what catches repeated *phrases*.
    if n_words >= 2:
        bigrams = [f"{tokens[i]} {tokens[i + 1]}" for i in range(n_words - 1)]
        bigram_ttr = len(set(bigrams)) / len(bigrams)
    else:
        bigram_ttr = 1.0
    stopword_ratio = (
        sum(1 for t in tokens if t in EN_STOPWORDS) / n_words
    ) if n_words else 0.0

    # Words that are neither grammatical glue nor filler. This is the most
    # direct measure of whether a comment says anything at all.
    content_words = [
        t for t in tokens
        if t not in EN_STOPWORDS and t not in FILLER_WORDS and not t.isdigit()
    ]
    content_word_count = len(set(content_words))

    # zlib on very short strings is dominated by header overhead, so only
    # trust the ratio once there is enough text for it to mean something.
    if n_chars >= 80:
        raw = stripped.encode('utf-8')
        compression_ratio = len(zlib.compress(raw, 6)) / len(raw)
    else:
        compression_ratio = 1.0

    run_match = REPEAT_RUN_RE.search(stripped)
    max_char_run = len(run_match.group(0)) if run_match else 0

    content_len = max(len(content), 1)
    return {
        'n_chars': n_chars,
        'n_words': n_words,
        'alpha_ratio': alpha_chars / content_len,
        'ttr': ttr,
        'stopword_ratio': stopword_ratio,
        'compression_ratio': compression_ratio,
        'max_char_run': max_char_run,
        'emoji_ratio': emoji_chars / n_chars,
        'caps_ratio': upper_chars / max(alpha_chars, 1),
        'digit_ratio': digit_chars / content_len,
        'punct_ratio': punct_chars / content_len,
        'url_count': url_count,
        'mention_count': mention_count,
        'top_token_ratio': top_token_ratio,
        'cjk_ratio': cjk_chars / content_len,
        'bigram_ttr': bigram_ttr,
        'content_word_count': content_word_count,
        'content_word_ratio': (content_word_count / n_words) if n_words else 0.0,
    }


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

@dataclass
class FilterVerdict:
    """Why an item was kept or dropped. Carries the signals so thresholds are tunable."""

    keep: bool
    quality: float = 0.0
    reasons: List[str] = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityConfig:
    """
    Thresholds for the noise filter.

    Defaults are tuned for English product discussion. Comments get the stricter
    ``min_comment_chars`` because that is where the junk concentrates.
    """

    min_chars: int = 25
    min_comment_chars: int = 30
    min_words: int = 5
    min_alpha_ratio: float = 0.45
    min_ttr: float = 0.42
    max_top_token_ratio: float = 0.35
    # Below the hard floor the text is a repeated phrase rather than a
    # sentence, which no amount of other signal should rescue.
    min_bigram_ttr_hard: float = 0.55
    min_bigram_ttr: float = 0.75
    min_compression_ratio: float = 0.28   # below this the text is near-repetitive
    max_emoji_ratio: float = 0.25
    max_caps_ratio: float = 0.65
    max_digit_ratio: float = 0.35
    max_punct_ratio: float = 0.40
    max_char_run: int = 8
    # Sentences use stopwords. Near-zero means keyword stuffing; near-one means
    # the comment is pure filler with no nouns in it.
    min_stopword_ratio: float = 0.10
    max_stopword_ratio: float = 0.82
    # Distinct words that are neither glue nor filler. Below this the comment
    # is fluent but empty, which the statistical signals cannot see.
    min_content_words: int = 3
    min_content_word_ratio: float = 0.22
    min_quality: float = 0.45


class NoiseFilter:
    """Stages 1 and 2: hard drops, then a 0..1 quality score."""

    def __init__(self, config: Optional[QualityConfig] = None):
        self.config = config or QualityConfig()

    def judge(self, item: CorpusItem) -> FilterVerdict:
        """Evaluate one item without mutating it."""
        text = item.full_text
        stripped = normalize(text)
        cfg = self.config
        reasons: List[str] = []

        # ---- stage 1: hard drops, no scoring ----
        if not stripped:
            return FilterVerdict(False, 0.0, ['empty'])

        for pattern in BOILERPLATE_PATTERNS:
            if pattern.search(stripped):
                return FilterVerdict(False, 0.0, ['boilerplate'])

        for pattern in SPAM_PATTERNS:
            if pattern.search(stripped):
                return FilterVerdict(False, 0.0, ['spam'])

        # Exact-match social noise, punctuation stripped
        bare = re.sub(r'[^\w\s]', '', stripped.lower()).strip()
        if bare in NOISE_EXACT:
            return FilterVerdict(False, 0.0, ['noise_phrase'])

        for pattern in NOISE_PATTERNS:
            if pattern.search(stripped):
                return FilterVerdict(False, 0.0, ['noise_pattern'])

        signals = text_signals(text)
        is_cjk = signals['cjk_ratio'] > 0.3

        min_chars = cfg.min_comment_chars if item.kind == KIND_COMMENT else cfg.min_chars
        if is_cjk:
            # CJK packs far more meaning per character
            min_chars = max(int(min_chars * 0.5), 10)

        if signals['n_chars'] < min_chars:
            return FilterVerdict(False, 0.0, ['too_short'], signals)

        if not is_cjk and signals['n_words'] < cfg.min_words:
            return FilterVerdict(False, 0.0, ['too_few_words'], signals)

        if signals['alpha_ratio'] < cfg.min_alpha_ratio and not is_cjk:
            return FilterVerdict(False, 0.0, ['not_prose'], signals)

        if signals['url_count'] > 0 and signals['n_words'] < 8:
            return FilterVerdict(False, 0.0, ['link_only'], signals)

        if signals['n_words'] >= 8 and signals['bigram_ttr'] < cfg.min_bigram_ttr_hard:
            return FilterVerdict(False, 0.0, ['phrase_repetition'], signals)

        if not is_cjk and signals['content_word_count'] < cfg.min_content_words:
            return FilterVerdict(False, 0.0, ['contentless'], signals)

        # ---- stage 2: soft quality score ----
        quality = 1.0

        def penalize(amount: float, reason: str) -> None:
            nonlocal quality
            quality -= amount
            reasons.append(reason)

        if signals['ttr'] < cfg.min_ttr:
            penalize(0.25, 'low_lexical_diversity')
        if signals['top_token_ratio'] > cfg.max_top_token_ratio:
            penalize(0.20, 'token_repetition')
        if signals['bigram_ttr'] < cfg.min_bigram_ttr:
            penalize(0.25, 'phrase_repetition')
        if signals['compression_ratio'] < cfg.min_compression_ratio:
            penalize(0.30, 'repetitive')
        if signals['max_char_run'] > cfg.max_char_run:
            penalize(0.20, 'char_run')
        if signals['emoji_ratio'] > cfg.max_emoji_ratio:
            penalize(0.25, 'emoji_heavy')
        if signals['caps_ratio'] > cfg.max_caps_ratio:
            penalize(0.15, 'shouting')
        if signals['digit_ratio'] > cfg.max_digit_ratio:
            penalize(0.15, 'digit_heavy')
        if signals['punct_ratio'] > cfg.max_punct_ratio:
            penalize(0.15, 'punctuation_heavy')

        if not is_cjk:
            if signals['stopword_ratio'] < cfg.min_stopword_ratio:
                penalize(0.20, 'keyword_stuffed')
            elif signals['stopword_ratio'] > cfg.max_stopword_ratio:
                penalize(0.30, 'contentless_filler')
            if signals['content_word_ratio'] < cfg.min_content_word_ratio:
                penalize(0.25, 'low_content_density')

        # Length bonus: longer comments carry more usable detail, capped so a
        # rambling essay does not automatically outrank a sharp two-liner.
        quality += min(signals['n_words'] / 200.0, 0.15)

        quality = max(0.0, min(1.0, quality))
        return FilterVerdict(quality >= cfg.min_quality, quality, reasons, signals)


# --------------------------------------------------------------------------
# Relevance
# --------------------------------------------------------------------------

class RelevanceScorer:
    """
    Stage 3: IDF-weighted overlap between an item and the topic terms.

    Document frequencies come from the fetched corpus itself, so a term that
    appears in every item (usually the search term) carries almost no weight
    and the score is driven by the specific vocabulary around it.

    Comments inherit a decayed share of their parent's relevance. Without that,
    "mine broke after a month too" scores zero and gets dropped, even though it
    is exactly the objection the report needs.
    """

    def __init__(
        self,
        topic_terms: Sequence[str],
        min_relevance: float = 0.12,
        parent_decay: float = 0.6,
    ):
        self.topic_tokens = self._expand_terms(topic_terms)
        self.min_relevance = min_relevance
        self.parent_decay = parent_decay
        self._idf: Dict[str, float] = {}
        self._fitted = False

    @staticmethod
    def _expand_terms(terms: Sequence[str]) -> List[str]:
        """Split multi-word topic terms into their component tokens."""
        expanded: List[str] = []
        for term in terms:
            expanded.extend(tokenize(term))
        # Preserve order, drop duplicates and bare stopwords
        seen = set()
        result = []
        for token in expanded:
            if token in seen or token in EN_STOPWORDS:
                continue
            seen.add(token)
            result.append(token)
        return result

    def fit(self, items: Iterable[CorpusItem]) -> 'RelevanceScorer':
        """Compute document frequencies over the corpus."""
        doc_freq: Counter = Counter()
        n_docs = 0
        for item in items:
            n_docs += 1
            doc_freq.update(set(tokenize(item.full_text)))
        if n_docs == 0:
            self._fitted = True
            return self
        for token, freq in doc_freq.items():
            # Smoothed IDF, floored at zero so a term in every document
            # contributes nothing rather than going negative.
            self._idf[token] = max(log((n_docs + 1) / (freq + 1)), 0.0)
        self._fitted = True
        return self

    def _idf_of(self, token: str) -> float:
        # Unseen topic tokens are rare by definition, so give them the max
        # weight observed rather than zero.
        return self._idf.get(token, 1.0)

    def score(self, item: CorpusItem) -> float:
        """Relevance of one item in 0..1, ignoring parent inheritance."""
        if not self.topic_tokens:
            return 1.0
        item_tokens = set(tokenize(item.full_text))
        if not item_tokens:
            return 0.0
        matched = sum(
            self._idf_of(t) for t in self.topic_tokens if t in item_tokens
        )
        total = sum(self._idf_of(t) for t in self.topic_tokens)
        if total <= 0:
            return 1.0
        coverage = matched / total
        # Long documents match more terms by chance; damp that mildly.
        length_damp = 1.0 / (1.0 + log(1 + len(item_tokens) / 400.0))
        return min(1.0, coverage * length_damp * 1.15)

    def score_all(self, items: Sequence[CorpusItem]) -> Dict[str, float]:
        """
        Score every item, then push parent relevance down to replies.

        Returns a mapping of ``item.uid`` to the effective relevance.
        """
        if not self._fitted:
            self.fit(items)

        own: Dict[str, float] = {item.uid: self.score(item) for item in items}
        by_local_id: Dict[str, CorpusItem] = {item.item_id: item for item in items}

        effective: Dict[str, float] = {}
        for item in items:
            value = own[item.uid]
            # A self-referencing parent_id would let an item bootstrap its own
            # relevance, so ignore it.
            parent = (
                by_local_id.get(item.parent_id)
                if item.parent_id and item.parent_id != item.item_id
                else None
            )
            if parent is not None:
                value = max(value, own.get(parent.uid, 0.0) * self.parent_decay)
            effective[item.uid] = max(value, item.provenance_relevance)
        return effective


# --------------------------------------------------------------------------
# Near-duplicate removal
# --------------------------------------------------------------------------

class SimHashIndex:
    """
    64-bit SimHash with banded lookup.

    Reddit reposts, copy-pasted reviews and bot chains produce large clusters of
    near-identical text. Comparing every pair is O(n^2); splitting the hash into
    four 16-bit bands and only comparing items that share a band makes it
    effectively O(n) while still catching everything within a Hamming distance
    of 3 (pigeonhole: 4 bands, at most 3 differing bits, so one band matches).
    """

    BANDS = 4
    BAND_BITS = 16

    def __init__(self, max_distance: int = 3, shingle_size: int = 3):
        self.max_distance = max_distance
        self.shingle_size = shingle_size
        self._buckets: List[Dict[int, List[Tuple[int, str]]]] = [
            {} for _ in range(self.BANDS)
        ]

    @classmethod
    def simhash(cls, text: str, shingle_size: int = 3) -> int:
        """Compute the 64-bit SimHash of a string over token shingles."""
        tokens = tokenize(text)
        if not tokens:
            return 0
        if len(tokens) >= shingle_size:
            shingles = [
                ' '.join(tokens[i:i + shingle_size])
                for i in range(len(tokens) - shingle_size + 1)
            ]
        else:
            shingles = tokens

        vector = [0] * 64
        for shingle, weight in Counter(shingles).items():
            # zlib.crc32 twice with different seeds gives a cheap 64-bit hash
            # without pulling in hashlib for every shingle.
            low = zlib.crc32(shingle.encode('utf-8')) & 0xFFFFFFFF
            high = zlib.crc32(shingle.encode('utf-8'), 0x9E3779B9) & 0xFFFFFFFF
            digest = (high << 32) | low
            for bit in range(64):
                vector[bit] += weight if (digest >> bit) & 1 else -weight

        result = 0
        for bit in range(64):
            if vector[bit] > 0:
                result |= (1 << bit)
        return result

    @staticmethod
    def hamming(a: int, b: int) -> int:
        return bin(a ^ b).count('1')

    def find_duplicate(self, fingerprint: int) -> Optional[str]:
        """Return the uid of an indexed near-duplicate, or None."""
        seen: set = set()
        for band in range(self.BANDS):
            key = (fingerprint >> (band * self.BAND_BITS)) & 0xFFFF
            for other_fp, other_uid in self._buckets[band].get(key, ()):
                if other_uid in seen:
                    continue
                seen.add(other_uid)
                if self.hamming(fingerprint, other_fp) <= self.max_distance:
                    return other_uid
        return None

    def add(self, fingerprint: int, uid: str) -> None:
        for band in range(self.BANDS):
            key = (fingerprint >> (band * self.BAND_BITS)) & 0xFFFF
            self._buckets[band].setdefault(key, []).append((fingerprint, uid))


def deduplicate(items: Sequence[CorpusItem], max_distance: int = 3) -> Tuple[List[CorpusItem], int]:
    """
    Drop near-duplicates, keeping the highest-scoring copy of each cluster.

    Args:
        items: Items to deduplicate
        max_distance: Hamming distance under which two items are the same

    Returns:
        ``(kept_items, dropped_count)``
    """
    # Process the strongest items first so the survivor of each cluster is the
    # one with the most engagement behind it.
    ordered = sorted(
        items,
        key=lambda i: (i.score, i.quality_score, len(i.full_text)),
        reverse=True,
    )
    index = SimHashIndex(max_distance=max_distance)
    kept: List[CorpusItem] = []
    dropped = 0

    for item in ordered:
        fingerprint = SimHashIndex.simhash(item.full_text)
        if fingerprint == 0:
            continue
        if index.find_duplicate(fingerprint) is not None:
            dropped += 1
            continue
        index.add(fingerprint, item.uid)
        kept.append(item)

    return kept, dropped


# --------------------------------------------------------------------------
# Combined pipeline
# --------------------------------------------------------------------------

@dataclass
class FilterStats:
    """Counts for every stage, so a bad filter run is diagnosable."""

    total_in: int = 0
    dropped_excluded: int = 0
    dropped_noise: int = 0
    dropped_irrelevant: int = 0
    dropped_duplicate: int = 0
    kept: int = 0
    drop_reasons: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        retention = (self.kept / self.total_in) if self.total_in else 0.0
        return {
            'total_in': self.total_in,
            'dropped_excluded': self.dropped_excluded,
            'dropped_noise': self.dropped_noise,
            'dropped_irrelevant': self.dropped_irrelevant,
            'dropped_duplicate': self.dropped_duplicate,
            'kept': self.kept,
            'retention_rate': round(retention, 4),
            'drop_reasons': dict(
                sorted(self.drop_reasons.items(), key=lambda kv: kv[1], reverse=True)
            ),
        }


class CorpusFilter:
    """Runs the full cheap-first cascade over a fetched corpus."""

    def __init__(
        self,
        topic_terms: Sequence[str],
        exclude_pattern: Optional[re.Pattern] = None,
        quality_config: Optional[QualityConfig] = None,
        min_relevance: float = 0.12,
        dedupe_distance: int = 3,
    ):
        self.noise = NoiseFilter(quality_config)
        self.relevance = RelevanceScorer(topic_terms, min_relevance=min_relevance)
        self.exclude_pattern = exclude_pattern
        self.min_relevance = min_relevance
        self.dedupe_distance = dedupe_distance

    def run(self, items: Sequence[CorpusItem]) -> Tuple[List[CorpusItem], FilterStats]:
        """
        Filter a corpus.

        Args:
            items: Everything the adapters fetched

        Returns:
            ``(kept_items, stats)`` with ``quality_score``, ``relevance_score``
            and ``signals`` populated on each kept item.
        """
        stats = FilterStats(total_in=len(items))

        def note(reason: str) -> None:
            stats.drop_reasons[reason] = stats.drop_reasons.get(reason, 0) + 1

        # Stage 0: brand blocklist. Runs first because contaminated items must
        # not influence the IDF table either.
        stage0: List[CorpusItem] = []
        for item in items:
            if self.exclude_pattern and self.exclude_pattern.search(item.full_text):
                stats.dropped_excluded += 1
                note('excluded_term')
                continue
            stage0.append(item)

        # Stages 1-2: noise and quality
        stage1: List[CorpusItem] = []
        for item in stage0:
            verdict = self.noise.judge(item)
            item.quality_score = verdict.quality
            item.signals = verdict.signals
            if not verdict.keep:
                stats.dropped_noise += 1
                note(verdict.reasons[0] if verdict.reasons else 'low_quality')
                continue
            stage1.append(item)

        # Stage 3: relevance, fitted on the surviving corpus
        scores = self.relevance.fit(stage1).score_all(stage1)
        stage2: List[CorpusItem] = []
        for item in stage1:
            item.relevance_score = scores.get(item.uid, 0.0)
            if item.relevance_score < self.min_relevance:
                stats.dropped_irrelevant += 1
                note('off_topic')
                continue
            stage2.append(item)

        # Stage 4: near-duplicate removal
        kept, dropped = deduplicate(stage2, self.dedupe_distance)
        stats.dropped_duplicate = dropped
        if dropped:
            stats.drop_reasons['near_duplicate'] = dropped
        stats.kept = len(kept)

        # Rank by combined usefulness so callers can take the top N directly
        kept.sort(
            key=lambda i: (i.quality_score * 0.4 + i.relevance_score * 0.6, i.score),
            reverse=True,
        )
        return kept, stats
