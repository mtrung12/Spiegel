"""\nConfiguration management.\nAll settings are loaded from the .env file in the project root.\n"""

import ipaddress
import os
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

# Load the .env file from the project root
# Path: Spiegel/.env (relative to backend/app/config.py)
project_root_env = os.path.join(PROJECT_ROOT, '.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # No .env in the project root: fall back to ambient environment variables (production)
    load_dotenv(override=True)


def load_yaml_config(name: str, env_var: str) -> dict:
    """
    Read one config/<name>.yml, or an override path from ``env_var``.

    Behaviour settings live in YAML so they can be read and reviewed; secrets
    stay in .env. A missing file is not an error - every value has a default.
    """
    path = os.environ.get(env_var) or os.path.join(PROJECT_ROOT, 'config', f'{name}.yml')
    try:
        with open(path, encoding='utf-8') as handle:
            return yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        # Bad config is worth shouting about: silently falling back to defaults
        # would quietly re-enable a source the operator switched off.
        raise RuntimeError(f"cannot read config file {path}: {exc}") from exc


_corpus_yaml = load_yaml_config('corpus', 'CORPUS_CONFIG_FILE')
_corpus_sources = _corpus_yaml.get('sources') or {}
_corpus_limits = _corpus_yaml.get('limits') or {}


_LOCAL_LLM_HOSTNAMES = {'localhost', '0.0.0.0', 'host.docker.internal'}

# Self-hosted servers (LM Studio, Ollama, vLLM, llama.cpp) ignore the API key,
# but the OpenAI SDK still refuses to start without a non-empty one.
LOCAL_LLM_PLACEHOLDER_KEY = 'local'


def is_local_llm_url(base_url: str | None) -> bool:
    """True when base_url points at a loopback, LAN, or .local host."""
    host = (urlparse(base_url or '').hostname or '').strip()
    if not host:
        return False
    if host in _LOCAL_LLM_HOSTNAMES or host.endswith('.local'):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def resolve_llm_api_key(api_key: str | None, base_url: str | None) -> str | None:
    """Return the key to use, substituting a placeholder for local servers."""
    if api_key:
        return api_key
    return LOCAL_LLM_PLACEHOLDER_KEY if is_local_llm_url(base_url) else None


class Config:
    """Flask configuration."""
    
    # Flask settings. SECRET_KEY has no default: a fallback baked into a public
    # repo is a known value everywhere it is not overridden, which is worse than
    # failing to start. validate() reports it as a missing setting like any other.
    SECRET_KEY = os.environ.get('SECRET_KEY')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON settings - disable ASCII escaping so non-ASCII text renders literally
    JSON_AS_ASCII = False
    
    # LLM settings (always called through the OpenAI format).
    # These drive report generation and the simulated audience agents.
    # Point LLM_BASE_URL at a local server (e.g. http://192.168.1.10:1234/v1 for
    # LM Studio) and LLM_API_KEY becomes optional.
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_API_KEY = resolve_llm_api_key(os.environ.get('LLM_API_KEY'), LLM_BASE_URL)
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')

    # Chatbot LLM - the follow-up Q&A in the workspace. Interactive, so it is
    # usually worth a faster or cheaper model than the agents run on. Each
    # value falls back to its LLM_* counterpart when unset. The key is inherited
    # only when both point at the same endpoint: one side on a local server and
    # the other on a cloud provider are separate credentials, and sending either
    # key to the other endpoint is at best a 401 and at worst a leaked key.
    CHATBOT_LLM_BASE_URL = os.environ.get('CHATBOT_LLM_BASE_URL') or LLM_BASE_URL
    CHATBOT_LLM_API_KEY = resolve_llm_api_key(
        os.environ.get('CHATBOT_LLM_API_KEY')
        or (LLM_API_KEY if CHATBOT_LLM_BASE_URL == LLM_BASE_URL else None),
        CHATBOT_LLM_BASE_URL,
    )
    CHATBOT_LLM_MODEL_NAME = os.environ.get('CHATBOT_LLM_MODEL_NAME') or LLM_MODEL_NAME

    # Simulation LLM - the OASIS agent loop, read by the simulation subprocess
    # rather than by this process. Worth its own entry because that loop is the
    # pipeline's dominant cost by a wide margin - rounds x active agents x
    # platforms, against tens of calls for everything else - while asking the
    # least of the model: pick one action from a listed action space. A cheap
    # model here and a stronger one for ontology, profiles and the report saves
    # a lot for little quality lost. Same inheritance and key rules as above.
    SIMULATION_LLM_BASE_URL = os.environ.get('SIMULATION_LLM_BASE_URL') or LLM_BASE_URL
    SIMULATION_LLM_API_KEY = resolve_llm_api_key(
        os.environ.get('SIMULATION_LLM_API_KEY')
        or (LLM_API_KEY if SIMULATION_LLM_BASE_URL == LLM_BASE_URL else None),
        SIMULATION_LLM_BASE_URL,
    )
    SIMULATION_LLM_MODEL_NAME = os.environ.get('SIMULATION_LLM_MODEL_NAME') or LLM_MODEL_NAME

    # Vision LLM - reads the pages of an uploaded PDF that carry no text layer.
    # A creative deck exported from Figma or Keynote is one image per slide, so
    # without this the brief extracts to nothing. Inherits LLM_* when unset, on
    # the same key rule as the chatbot above; leave it inherited only when the
    # main model can actually see images.
    VISION_LLM_BASE_URL = os.environ.get('VISION_LLM_BASE_URL') or LLM_BASE_URL
    VISION_LLM_API_KEY = resolve_llm_api_key(
        os.environ.get('VISION_LLM_API_KEY')
        or (LLM_API_KEY if VISION_LLM_BASE_URL == LLM_BASE_URL else None),
        VISION_LLM_BASE_URL,
    )
    VISION_LLM_MODEL_NAME = os.environ.get('VISION_LLM_MODEL_NAME') or LLM_MODEL_NAME

    # Rasterisation DPI for those pages, and the cap on how many get sent. The
    # cap is a bill guard: a 300-page scan would otherwise be 300 vision calls.
    VISION_PDF_DPI = int(os.environ.get('VISION_PDF_DPI', '150'))
    VISION_PDF_MAX_PAGES = int(os.environ.get('VISION_PDF_MAX_PAGES', '40'))

    # Zep settings
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')

    # Embeddings for the content vector index. Served by the self-hosted LM Studio
    # box in the OpenAI format, so no per-post bill and no data leaving the LAN.
    # A private base URL supplies its own key, hence no EMBEDDING_API_KEY below in
    # the default case - see resolve_llm_api_key.
    #
    # Posts and comments are short, so the model choice is about multilingual
    # coverage (the UI ships en + sk and the agents write in the campaign's
    # language), not context length. Changing the model changes the vector length,
    # so the collection is rebuilt on the next search - see
    # content_index._ensure_collection.
    EMBEDDING_BASE_URL = os.environ.get(
        'EMBEDDING_BASE_URL', 'http://172.24.247.130:1234/v1'
    )
    EMBEDDING_API_KEY = resolve_llm_api_key(
        os.environ.get('EMBEDDING_API_KEY')
        or (LLM_API_KEY if EMBEDDING_BASE_URL == LLM_BASE_URL else None),
        EMBEDDING_BASE_URL,
    )
    # Qwen3-Embedding-4B: 2560 dimensions, multilingual, retrieval-trained and
    # symmetric, so queries and stored records need no prefixes.
    #
    # Chosen over BGE-M3 on a measurement against the live box: bge-m3 as served
    # there ranked a Slovak price complaint below an unrelated English post, for
    # an English *and* a Slovak query, while this model ranked the same corpus
    # correctly in both. Non-English recall matters because the UI ships en + sk.
    # `text-embedding-bge-m3` (1024d) is also loaded there if you want to compare
    # again - the index rebuilds itself on the switch.
    EMBEDDING_MODEL_NAME = os.environ.get(
        'EMBEDDING_MODEL_NAME', 'text-embedding-qwen3-embedding-4b'
    )

    # Qdrant. Unset QDRANT_URL means embedded local storage under uploads/ -
    # no server to run. Point it at a Qdrant instance for multi-worker setups.
    QDRANT_URL = os.environ.get('QDRANT_URL')
    QDRANT_API_KEY = os.environ.get('QDRANT_API_KEY')
    QDRANT_PATH = os.environ.get('QDRANT_PATH') or os.path.join(
        os.path.dirname(__file__), '../uploads/qdrant'
    )

    # File upload settings
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # Text processing settings
    DEFAULT_CHUNK_SIZE = 500  # Default chunk size
    DEFAULT_CHUNK_OVERLAP = 50  # Default chunk overlap
    
    # How the cloned part of the cast splits between generic company accounts
    # and generic personal ones: 10% companies, 90% people. Applied to the
    # slots left after every specific (named) entity has taken its one agent.
    #
    # A calibration knob, not a measured constant. The nearest public figures
    # are account-registration shares - Instagram ~200M business accounts of
    # 2B+ users (~10%), LinkedIn 67.1M company pages of ~1B members (~6.7%) -
    # and what this actually governs is the share of accounts that post a
    # reaction in one category, which nobody publishes. Raise it for B2B or
    # trade-press-heavy categories, lower it where brands barely speak.
    GENERAL_COMPANY_SHARE = float(os.environ.get('GENERAL_COMPANY_SHARE', '0.10'))

    # OASIS simulation settings
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # Actions available on each OASIS platform
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Corpus settings (public-discussion harvesting). Behaviour comes from
    # config/corpus.yml; the CORPUS_* environment variables still win, which is
    # how a deployment overrides a file it cannot edit.
    CORPUS_USER_AGENT = os.environ.get('CORPUS_USER_AGENT') or _corpus_limits.get(
        'user_agent', 'Spiegel/0.1 (marketing-campaign research)'
    )
    CORPUS_HTTP_TIMEOUT = float(
        os.environ.get('CORPUS_HTTP_TIMEOUT') or _corpus_limits.get('http_timeout', 30)
    )
    CORPUS_RATE_PER_SECOND = float(
        os.environ.get('CORPUS_RATE_PER_SECOND') or _corpus_limits.get('rate_per_second', 1.0)
    )
    CORPUS_MAX_RESPONSE_BYTES = int(
        os.environ.get('CORPUS_MAX_RESPONSE_BYTES')
        or _corpus_limits.get('max_response_bytes', 16 * 1024 * 1024)
    )
    # Author handles are hashed with this salt and never stored raw. Changing it
    # invalidates existing pseudonyms, which is the intended way to rotate.
    CORPUS_AUTHOR_SALT = os.environ.get('CORPUS_AUTHOR_SALT', 'campaign-reaction-default-salt')
    # Sources switched on in config/corpus.yml. None means the file said
    # nothing, so every registered source runs - but a file that switches them
    # all off means exactly that, and harvests nothing.
    CORPUS_SOURCES = (
        [name for name, enabled in _corpus_sources.items() if enabled]
        if _corpus_sources else None
    )

    # Reddit official OAuth API - free "script" app at reddit.com/prefs/apps
    REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID')
    REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET')

    # Threads has no public search API, so that source renders pages in a
    # headless browser. It is off in config/corpus.yml and these settings only
    # matter once it is switched on.
    #
    # Threads' robots.txt disallows the paths this source reads. Left true, the
    # adapter declines to fetch and says so; setting it false is a
    # terms-of-service decision that belongs to whoever runs the deployment.
    THREADS_RESPECT_ROBOTS = (
        os.environ.get('THREADS_RESPECT_ROBOTS', 'true').strip().lower()
        not in ('false', '0', 'no')
    )
    # A signed-out client gets a login wall, so a session cookie is required.
    # Accepts a browser cookie export (list) or a flat {name: value} map.
    THREADS_COOKIES_FILE = os.environ.get(
        'THREADS_COOKIES_FILE',
        os.path.expanduser('~/.config/threads-auth/credential.json'),
    )
    # headless-new keeps the real graphics pipeline, which is what pages that
    # branch on canvas/WebGL support need. 'headed'/'headless' are for debugging.
    THREADS_BROWSER_MODE = os.environ.get('THREADS_BROWSER_MODE', 'headless-new')
    # Scroll passes per page. Threads lazy-loads roughly 5-10 items per pass, so
    # this is the real cap on how much one search returns.
    THREADS_SCROLLS = int(os.environ.get('THREADS_SCROLLS', '4'))
    THREADS_REPLY_SCROLLS = int(os.environ.get('THREADS_REPLY_SCROLLS', '5'))
    # How many posts get opened for their reply chains. Each one is a page load
    # of several seconds, so this is the setting that decides harvest duration.
    THREADS_MAX_DETAIL_POSTS = int(os.environ.get('THREADS_MAX_DETAIL_POSTS', '10'))

    # Report agent settings
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required configuration."""
        errors: list[str] = []
        if not cls.SECRET_KEY:
            errors.append(
                "SECRET_KEY is not configured"
                " (generate one with: python -c \"import secrets; print(secrets.token_hex(32))\")"
            )
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY is not configured")
        # Only a chatbot on its own endpoint needs its own key. Sharing the
        # endpoint means the key is inherited, and a missing one is already
        # reported as LLM_API_KEY above - do not report the same gap twice.
        if cls.CHATBOT_LLM_BASE_URL != cls.LLM_BASE_URL and not cls.CHATBOT_LLM_API_KEY:
            errors.append(
                "CHATBOT_LLM_API_KEY is not configured"
                " (CHATBOT_LLM_BASE_URL differs from LLM_BASE_URL, so it needs its own key)"
            )
        # Same rule for the vision model. Only reached when the operator pointed
        # VISION_LLM_BASE_URL at its own host, which is a misconfiguration rather
        # than an opt-out: leaving every VISION_LLM_* unset inherits the main
        # endpoint and its key, and reports nothing here.
        if cls.VISION_LLM_BASE_URL != cls.LLM_BASE_URL and not cls.VISION_LLM_API_KEY:
            errors.append(
                "VISION_LLM_API_KEY is not configured"
                " (VISION_LLM_BASE_URL differs from LLM_BASE_URL, so it needs its own key)"
            )
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY is not configured")
        if os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL is not supported; Spiegel only connects to Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors
