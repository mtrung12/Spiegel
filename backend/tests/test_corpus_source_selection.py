"""
Which corpus sources a harvest is allowed to run.

Selection is three-tiered - explicit argument, config/corpus.yml, then every
registered source - and getting the precedence wrong either silently drops a
source the operator paid for or fetches one they switched off. The empty case
matters most: a file that disables everything must harvest nothing, not fall
back to running everything.
"""

import textwrap

import pytest

from app.config import Config, load_yaml_config
from app.services.corpus.harvester import CorpusHarvester
from app.services.corpus.sources import DEFAULT_SOURCES, SOURCE_REGISTRY

REPO_CONFIG = load_yaml_config('corpus', 'CORPUS_CONFIG_FILE')


def _write_config(tmp_path, monkeypatch, body):
    path = tmp_path / 'corpus.yml'
    path.write_text(textwrap.dedent(body), encoding='utf-8')
    monkeypatch.setenv('CORPUS_CONFIG_FILE', str(path))
    return load_yaml_config('corpus', 'CORPUS_CONFIG_FILE')


def test_shipped_config_names_only_real_sources():
    # A typo here would silently disable a crawler, so pin the names.
    assert set(REPO_CONFIG['sources']) == set(SOURCE_REGISTRY)


def test_default_covers_every_registered_source():
    # Credentialed sources skip themselves at fetch time when unconfigured, so
    # the default has no reason to exclude them up front.
    assert set(DEFAULT_SOURCES) == set(SOURCE_REGISTRY)


def test_shipped_config_runs_reddit_only():
    # The whole point of the current setup: Reddit on, everything else off.
    enabled = [name for name, on in REPO_CONFIG['sources'].items() if on]
    assert enabled == ['reddit']
    assert Config.CORPUS_SOURCES == ['reddit']


def test_config_file_switches_sources_off(tmp_path, monkeypatch):
    loaded = _write_config(tmp_path, monkeypatch, """
        sources:
          reddit: false
    """)
    enabled = [name for name, on in loaded['sources'].items() if on]
    monkeypatch.setattr(Config, 'CORPUS_SOURCES', enabled)
    assert CorpusHarvester().source_names == []


def test_everything_off_harvests_nothing(monkeypatch):
    monkeypatch.setattr(Config, 'CORPUS_SOURCES', [])
    assert CorpusHarvester().source_names == []


def test_missing_file_falls_back_to_every_source(tmp_path, monkeypatch):
    monkeypatch.setenv('CORPUS_CONFIG_FILE', str(tmp_path / 'absent.yml'))
    assert load_yaml_config('corpus', 'CORPUS_CONFIG_FILE') == {}
    monkeypatch.setattr(Config, 'CORPUS_SOURCES', None)
    assert set(CorpusHarvester().source_names) == set(DEFAULT_SOURCES)


def test_explicit_sources_beat_the_file(monkeypatch):
    monkeypatch.setattr(Config, 'CORPUS_SOURCES', [])
    assert CorpusHarvester(sources=['reddit']).source_names == ['reddit']


def test_broken_file_raises_instead_of_silently_defaulting(tmp_path, monkeypatch):
    path = tmp_path / 'corpus.yml'
    path.write_text('sources: [unclosed\n', encoding='utf-8')
    monkeypatch.setenv('CORPUS_CONFIG_FILE', str(path))
    with pytest.raises(RuntimeError):
        load_yaml_config('corpus', 'CORPUS_CONFIG_FILE')


def test_limits_come_from_the_file():
    limits = REPO_CONFIG['limits']
    assert limits['rate_per_second'] == Config.CORPUS_RATE_PER_SECOND
    assert limits['user_agent'] == Config.CORPUS_USER_AGENT
