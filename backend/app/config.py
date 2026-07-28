"""\nConfiguration management.\nAll settings are loaded from the .env file in the project root.\n"""

import ipaddress
import os
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))

# Load the .env file from the project root
# Path: MiroFish/.env (relative to backend/app/config.py)
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
    
    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
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

    # Zep settings
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')

    # Embeddings for the content vector index. Called through the OpenAI format
    # like every other model, and inherits the LLM_* endpoint when unset. The key
    # is inherited only when both point at the same endpoint - see the chatbot
    # note above for why.
    EMBEDDING_BASE_URL = os.environ.get('EMBEDDING_BASE_URL') or LLM_BASE_URL
    EMBEDDING_API_KEY = resolve_llm_api_key(
        os.environ.get('EMBEDDING_API_KEY')
        or (LLM_API_KEY if EMBEDDING_BASE_URL == LLM_BASE_URL else None),
        EMBEDDING_BASE_URL,
    )
    EMBEDDING_MODEL_NAME = os.environ.get('EMBEDDING_MODEL_NAME', 'text-embedding-3-small')

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
        'user_agent', 'CampaignReaction/0.1 (research; +https://github.com/666ghj/MiroFish)'
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

    # Report agent settings
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """Validate required configuration."""
        errors: list[str] = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未配置")
        # Only a chatbot on its own endpoint needs its own key. Sharing the
        # endpoint means the key is inherited, and a missing one is already
        # reported as LLM_API_KEY above - do not report the same gap twice.
        if cls.CHATBOT_LLM_BASE_URL != cls.LLM_BASE_URL and not cls.CHATBOT_LLM_API_KEY:
            errors.append(
                "CHATBOT_LLM_API_KEY 未配置"
                "（CHATBOT_LLM_BASE_URL 与 LLM_BASE_URL 不同，需要单独的密钥）"
            )
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未配置")
        if os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL 不受支持；MiroFish 仅连接 Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors
