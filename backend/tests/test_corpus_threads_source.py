"""
The Threads corpus source.

Two things are worth testing here and neither needs a browser. The parser turns
the JSON Threads hydrates its pages with into posts, and it has to stitch an
author's multi-part story back together without swallowing other people's
replies - get that wrong and the corpus fills with sentence fragments. The
adapter then has to map those into CorpusItem while keeping the promises the rest
of the corpus makes: pseudonymised handles, no stored permalinks, and no fetch at
all until robots.txt and the config file both allow it.

The browser transport itself is not exercised - it is Playwright driving a real
Chromium, which belongs in a manual check, not a test run.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Config, load_yaml_config
from app.services.corpus.models import KIND_COMMENT, KIND_POST, SourceQuery
from app.services.corpus.sources import (
    BROWSER_SOURCES,
    SOURCE_REGISTRY,
    describe_sources,
    get_source,
)
from app.services.corpus.sources import threads as threads_module
from app.services.corpus.sources import threads_browser, threads_parser
from app.services.corpus.sources.threads import ThreadsSource

NOW = datetime.now(timezone.utc)


# -- fixtures --------------------------------------------------------------


def _post(
    post_id,
    text,
    *,
    username='alice',
    user_pk='pk_alice',
    code=None,
    taken_at=None,
    likes=0,
    replies=0,
    reposts=0,
):
    """One entry as Threads ships it inside `thread_items`."""
    return {
        'post': {
            'id': post_id,
            'pk': post_id,
            'code': code or f'code_{post_id}',
            'caption': {'text': text},
            'taken_at': int((taken_at or NOW).timestamp()),
            'like_count': likes,
            'text_post_app_info': {
                'direct_reply_count': replies,
                'repost_count': reposts,
            },
            'user': {'username': username, 'pk': user_pk, 'is_verified': False},
        }
    }


def _page(*chains):
    """Wrap chains of entries in the script tag the parser looks for."""
    payload = {'data': [{'thread_items': list(chain)} for chain in chains]}
    return (
        '<html><body>'
        f'<script type="application/json" data-sjs>{json.dumps(payload)}</script>'
        '</body></html>'
    )


class FakeRenderer:
    """Stands in for the browser: hands back canned HTML, counts page loads."""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.pages_rendered = 0
        self.batches = []

    async def render_many(self, urls, scrolls=3, on_page=None):
        self.batches.append(list(urls))
        rendered = {}
        for url in urls:
            self.pages_rendered += 1
            if url in self.pages:
                rendered[url] = self.pages[url]
        return rendered


class FakeClient:
    """A PoliteClient that answers robots questions without a network."""

    def __init__(self, allowed=True):
        self.allowed = allowed
        self.asked = []

    def allows(self, url):
        self.asked.append(url)
        return self.allowed

    def close(self):
        pass


def _search_url(term):
    from urllib.parse import quote

    return threads_module.SEARCH_URL.format(query=quote(term))


@pytest.fixture(autouse=True)
def _threads_settings(monkeypatch):
    """Deterministic settings: robots off, generous page budget."""
    monkeypatch.setattr(Config, 'THREADS_RESPECT_ROBOTS', False)
    monkeypatch.setattr(Config, 'THREADS_MAX_DETAIL_POSTS', 10)
    monkeypatch.setattr(Config, 'THREADS_SCROLLS', 1)
    monkeypatch.setattr(Config, 'THREADS_REPLY_SCROLLS', 1)


def _adapter(pages, allowed=True):
    renderer = FakeRenderer(pages)
    client = FakeClient(allowed=allowed)
    return ThreadsSource(client=client, renderer=renderer), renderer, client


# -- parser ----------------------------------------------------------------


def test_parser_reads_a_single_post():
    html = _page([_post('1', 'core banking is a nightmare to migrate', likes=12, replies=3)])
    threads = threads_parser.extract_thread_list(html)

    assert len(threads) == 1
    assert threads[0]['text'] == 'core banking is a nightmare to migrate'
    assert threads[0]['like_count'] == 12
    assert threads[0]['reply_count'] == 3


def test_parser_stitches_the_authors_own_continuation():
    # The whole reason this parser is not a one-liner: a story split across
    # posts is one item, not three fragments.
    html = _page([
        _post('1', '1/3 we started the migration in March'),
        _post('2', '2/3 by June the vendor had missed two deadlines'),
        _post('3', '3/3 we are still running both cores'),
    ])
    text = threads_parser.extract_thread_list(html)[0]['text']

    assert text == (
        'we started the migration in March\n\n'
        'by June the vendor had missed two deadlines\n\n'
        'we are still running both cores'
    )


def test_parser_does_not_stitch_someone_elses_reply():
    html = _page([
        _post('1', 'we started the migration in March'),
        _post('2', 'same here, it took us two years', username='bob', user_pk='pk_bob'),
    ])
    threads = threads_parser.extract_thread_list(html)

    assert threads[0]['text'] == 'we started the migration in March'


def test_parser_skips_entries_with_no_text():
    html = _page([_post('1', ''), _post('2', 'real content')])
    threads = threads_parser.extract_thread_list(html)

    # The empty root drops out, so the text-bearing post becomes the root.
    assert [t['text'] for t in threads] == ['real content']


def test_part_marker_stripping_leaves_the_middle_alone():
    assert threads_parser.strip_part_marker('(2/5) the vendor went quiet') == 'the vendor went quiet'
    assert threads_parser.strip_part_marker('Part 3: then it broke') == 'then it broke'
    assert threads_parser.strip_part_marker('a story 1/2') == 'a story'
    # A ratio inside a sentence is content, not scaffolding.
    assert threads_parser.strip_part_marker('we cut costs 1/3 that year') == (
        'we cut costs 1/3 that year'
    )


def test_parser_ignores_a_malformed_blob_rather_than_raising():
    html = (
        '<html><body>'
        '<script type="application/json" data-sjs>{"thread_items": broken</script>'
        + _page([_post('1', 'still parsed')])
        + '</body></html>'
    )
    assert [t['text'] for t in threads_parser.extract_thread_list(html)] == ['still parsed']


def test_parser_returns_nothing_for_a_page_with_no_blobs():
    assert threads_parser.extract_thread_list('<html><body>login</body></html>') == []


def test_post_page_splits_continuations_from_replies():
    html = _page(
        [_post('1', 'the RFP process took eleven months')],
        [_post('2', 'and that was before legal got involved')],  # same author
        [_post('3', 'ours took two years', username='bob', user_pk='pk_bob')],
    )
    bundle = threads_parser.extract_post_page(html)

    assert bundle['thread']['text'] == (
        'the RFP process took eleven months\n\nand that was before legal got involved'
    )
    assert [r['text'] for r in bundle['replies']] == ['ours took two years']


def test_post_page_handles_an_empty_document():
    assert threads_parser.extract_post_page('<html></html>') == {'thread': None, 'replies': []}


def test_post_url_needs_both_handle_and_code():
    assert threads_parser.post_url({'username': 'alice', 'code': 'AbC'}) == (
        'https://www.threads.net/@alice/post/AbC'
    )
    assert threads_parser.post_url({'username': 'alice'}) is None


# -- cookies ---------------------------------------------------------------


def test_cookies_load_from_a_flat_map(tmp_path):
    path = tmp_path / 'cred.json'
    path.write_text(json.dumps({'sessionid': 'abc', 'blank': ''}), encoding='utf-8')

    cookies = threads_browser.load_cookies(str(path))

    assert [c['name'] for c in cookies] == ['sessionid']
    assert cookies[0]['domain'] == '.threads.net'


def test_cookies_load_from_a_browser_export(tmp_path):
    path = tmp_path / 'cred.json'
    path.write_text(
        json.dumps([
            {'name': 'sessionid', 'value': 'abc', 'sameSite': 'no_restriction'},
        ]),
        encoding='utf-8',
    )

    cookies = threads_browser.load_cookies(str(path))

    # Playwright rejects the extension-style value, so it has to be normalised.
    assert cookies[0]['sameSite'] == 'Lax'


def test_missing_or_broken_cookie_file_yields_nothing(tmp_path):
    assert threads_browser.load_cookies(str(tmp_path / 'absent.json')) == []
    broken = tmp_path / 'broken.json'
    broken.write_text('{not json', encoding='utf-8')
    assert threads_browser.load_cookies(str(broken)) == []


def test_cookies_load_from_a_wrapped_export(tmp_path):
    path = tmp_path / 'cred.json'
    path.write_text(json.dumps({'cookies': {'sessionid': 'abc'}}), encoding='utf-8')

    assert [c['name'] for c in threads_browser.load_cookies(str(path))] == ['sessionid']


# -- browser transport (the parts that need no browser) --------------------


def test_headless_new_does_not_ask_playwright_for_headless():
    # Playwright appends its own legacy --headless when told headless=True,
    # which selects the old renderer and defeats the point of this mode.
    options = threads_browser.launch_args('headless-new')

    assert options['headless'] is False
    assert '--headless=new' in options['args']


def test_other_browser_modes_are_plain():
    assert threads_browser.launch_args('headless')['headless'] is True
    assert threads_browser.launch_args('headed')['headless'] is False


def test_pointer_path_starts_and_ends_where_asked():
    path = threads_browser._mouse_path(0, 0, 400, 300)

    assert path[0] == (0, 0)
    assert path[-1] == pytest.approx((400, 300))
    assert len(path) > 2  # a curve, not a jump


def test_run_sync_surfaces_the_error_on_the_calling_thread():
    async def boom():
        raise ValueError('rendering failed')

    with pytest.raises(ValueError, match='rendering failed'):
        threads_browser.run_sync(boom)


# -- adapter ---------------------------------------------------------------


def test_fetch_maps_posts_and_replies_onto_corpus_items():
    term = 'core banking migration'
    post_html = _page([_post('1', 'migrating our core took two years', likes=40, replies=2)])
    detail_html = _page(
        [_post('1', 'migrating our core took two years', likes=40, replies=2)],
        [_post('9', 'we gave up and stayed on the old one', username='bob', user_pk='pk_bob',
               likes=5)],
    )
    adapter, renderer, _ = _adapter({
        _search_url(term): post_html,
        'https://www.threads.net/@alice/post/code_1': detail_html,
    })

    result = adapter.fetch(SourceQuery(terms=[term], max_comments_per_post=5))

    posts = [i for i in result.items if i.kind == KIND_POST]
    comments = [i for i in result.items if i.kind == KIND_COMMENT]
    assert len(posts) == 1 and len(comments) == 1
    assert posts[0].item_id == 'th_1'
    assert posts[0].score == 40
    assert posts[0].reply_count == 2
    assert posts[0].channel == 'threads'
    assert comments[0].parent_id == 'th_1'
    assert comments[0].text == 'we gave up and stayed on the old one'
    assert result.requests_made == renderer.pages_rendered == 2
    assert result.errors == []


def test_fetch_pseudonymises_the_handle_and_stores_no_permalink():
    # The promise the rest of the corpus makes: no raw handles, anywhere. A
    # Threads permalink contains one, so it is dropped rather than stored.
    term = 'digital onboarding'
    adapter, _, _ = _adapter({_search_url(term): _page([_post('1', 'onboarding is broken')])})

    item = adapter.fetch(SourceQuery(terms=[term], max_comments_per_post=0)).items[0]

    assert item.url is None
    assert item.author_pseudonym is not None
    assert 'alice' not in json.dumps(item.to_dict())
    assert item.meta['code'] == 'code_1'


def test_fetch_converts_timestamps_to_rfc3339():
    term = 'kyc'
    when = NOW - timedelta(days=3)
    adapter, _, _ = _adapter({
        _search_url(term): _page([_post('1', 'kyc checks take forever', taken_at=when)])
    })

    item = adapter.fetch(SourceQuery(terms=[term], max_comments_per_post=0)).items[0]

    assert datetime.fromisoformat(item.created_at).tzinfo is not None


def test_fetch_drops_posts_older_than_the_window():
    term = 'legacy core'
    html = _page(
        [_post('1', 'this happened last week', taken_at=NOW - timedelta(days=7))],
        [_post('2', 'this happened years ago', taken_at=NOW - timedelta(days=900))],
    )
    adapter, _, _ = _adapter({_search_url(term): html})

    result = adapter.fetch(
        SourceQuery(terms=[term], since_days=30, max_comments_per_post=0)
    )

    assert [i.item_id for i in result.items] == ['th_1']


def test_fetch_searches_each_term_separately_and_dedupes():
    # Threads has no boolean syntax, so terms cannot be joined into one query -
    # and the same post surfacing under two terms must not be counted twice.
    html = _page([_post('1', 'shared result')])
    adapter, renderer, _ = _adapter({
        _search_url('open banking'): html,
        _search_url('psd2'): html,
    })

    result = adapter.fetch(
        SourceQuery(terms=['open banking', 'psd2'], max_comments_per_post=0)
    )

    assert len(renderer.batches[0]) == 2
    assert [i.item_id for i in result.items] == ['th_1']


def test_fetch_respects_max_items():
    term = 'fintech'
    html = _page(*[[_post(str(n), f'post number {n}')] for n in range(6)])
    adapter, _, _ = _adapter({_search_url(term): html})

    result = adapter.fetch(
        SourceQuery(terms=[term], max_items=3, max_comments_per_post=0)
    )

    assert len(result.items) == 3
    assert result.truncated is True


def test_fetch_skips_reply_pages_when_comments_are_not_wanted():
    term = 'fintech'
    adapter, renderer, _ = _adapter({_search_url(term): _page([_post('1', 'a post')])})

    adapter.fetch(SourceQuery(terms=[term], max_comments_per_post=0))

    assert renderer.pages_rendered == 1


def test_fetch_reports_a_login_wall_rather_than_returning_silence():
    term = 'fintech'
    adapter, _, _ = _adapter({_search_url(term): '<html><body>Log in</body></html>'})

    result = adapter.fetch(SourceQuery(terms=[term]))

    assert result.items == []
    assert any('session cookie' in e or 'login wall' in e for e in result.errors)


def test_fetch_needs_terms():
    adapter, renderer, _ = _adapter({})

    result = adapter.fetch(SourceQuery(terms=['   ']))

    assert result.errors == ['no search terms provided']
    assert renderer.pages_rendered == 0


def test_a_failed_render_is_an_error_not_an_exception():
    term = 'fintech'
    adapter, _, _ = _adapter({})  # nothing renders

    result = adapter.fetch(SourceQuery(terms=[term]))

    assert result.items == []
    assert any('did not render' in e for e in result.errors)


# -- robots ----------------------------------------------------------------


def test_robots_disallow_stops_the_fetch_before_the_browser_starts(monkeypatch):
    monkeypatch.setattr(Config, 'THREADS_RESPECT_ROBOTS', True)
    term = 'fintech'
    adapter, renderer, client = _adapter(
        {_search_url(term): _page([_post('1', 'a post')])}, allowed=False
    )

    result = adapter.fetch(SourceQuery(terms=[term]))

    assert result.items == []
    assert any('robots.txt disallows' in e for e in result.errors)
    assert renderer.pages_rendered == 0
    assert client.asked  # it actually checked


def test_robots_allow_lets_the_fetch_run(monkeypatch):
    monkeypatch.setattr(Config, 'THREADS_RESPECT_ROBOTS', True)
    term = 'fintech'
    adapter, _, client = _adapter(
        {_search_url(term): _page([_post('1', 'a post')])}, allowed=True
    )

    result = adapter.fetch(SourceQuery(terms=[term], max_comments_per_post=0))

    assert len(result.items) == 1
    assert client.asked


def test_an_unreadable_robots_file_is_not_permission(monkeypatch):
    monkeypatch.setattr(Config, 'THREADS_RESPECT_ROBOTS', True)
    term = 'fintech'
    adapter, renderer, client = _adapter({_search_url(term): _page([_post('1', 'a post')])})

    def explode(url):
        raise RuntimeError('connection reset')

    client.allows = explode
    result = adapter.fetch(SourceQuery(terms=[term]))

    assert renderer.pages_rendered == 0
    assert any('robots.txt check failed' in e for e in result.errors)


def test_the_override_skips_the_check_entirely():
    # THREADS_RESPECT_ROBOTS is false via the autouse fixture.
    term = 'fintech'
    adapter, _, client = _adapter(
        {_search_url(term): _page([_post('1', 'a post')])}, allowed=False
    )

    result = adapter.fetch(SourceQuery(terms=[term], max_comments_per_post=0))

    assert len(result.items) == 1
    assert client.asked == []


# -- availability ----------------------------------------------------------


def test_unavailable_without_playwright(monkeypatch):
    monkeypatch.setattr(threads_browser, 'playwright_available', lambda: False)
    available, reason = ThreadsSource().is_available()

    assert available is False
    assert 'playwright' in reason


def test_unavailable_without_a_session_cookie(monkeypatch, tmp_path):
    monkeypatch.setattr(threads_browser, 'playwright_available', lambda: True)
    monkeypatch.setattr(Config, 'THREADS_COOKIES_FILE', str(tmp_path / 'absent.json'))
    available, reason = ThreadsSource().is_available()

    assert available is False
    assert 'no usable Threads session' in reason


def test_unavailable_source_returns_the_reason_instead_of_fetching(monkeypatch):
    monkeypatch.setattr(threads_browser, 'playwright_available', lambda: False)
    result = ThreadsSource().fetch(SourceQuery(terms=['fintech']))

    assert result.items == []
    assert 'playwright' in result.errors[0]


# -- config wiring ---------------------------------------------------------


def test_threads_is_registered_as_a_browser_source():
    assert 'threads' in SOURCE_REGISTRY
    assert 'threads' in BROWSER_SOURCES
    assert isinstance(get_source('threads'), ThreadsSource)


def test_shipped_config_lists_threads_and_leaves_it_off():
    # The registry-vs-file check in test_corpus_source_selection would catch a
    # missing key; this catches the likelier mistake of shipping it enabled.
    config = load_yaml_config('corpus', 'CORPUS_CONFIG_FILE')

    assert config['sources']['threads'] is False
    assert 'threads' not in (Config.CORPUS_SOURCES or [])


def test_config_file_can_switch_threads_on(tmp_path, monkeypatch):
    path = tmp_path / 'corpus.yml'
    path.write_text('sources:\n  reddit: false\n  threads: true\n', encoding='utf-8')
    monkeypatch.setenv('CORPUS_CONFIG_FILE', str(path))

    loaded = load_yaml_config('corpus', 'CORPUS_CONFIG_FILE')
    enabled = [name for name, on in loaded['sources'].items() if on]

    assert enabled == ['threads']


def test_describe_sources_explains_what_threads_needs(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, 'THREADS_COOKIES_FILE', str(tmp_path / 'absent.json'))
    entry = next(s for s in describe_sources() if s['name'] == 'threads')

    assert entry['requires_credentials'] is True
    assert entry['available'] is False
    assert 'THREADS_COOKIES_FILE' in entry['access_note']
