"""\nConfiguration management.\nAll settings are loaded from the .env file in the project root.\n"""

import os
from dotenv import load_dotenv

# Load the .env file from the project root
# Path: MiroFish/.env (relative to backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # No .env in the project root: fall back to ambient environment variables (production)
    load_dotenv(override=True)


class Config:
    """Flask configuration."""
    
    # Flask settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # JSON settings - disable ASCII escaping so non-ASCII text renders literally
    JSON_AS_ASCII = False
    
    # LLM settings (always called through the OpenAI format)
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Zep settings
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
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
    
    # Corpus settings (public-discussion harvesting)
    # Identify the client honestly - several APIs reject generic user agents,
    # and a contactable UA is what makes the traffic defensible.
    CORPUS_USER_AGENT = os.environ.get(
        'CORPUS_USER_AGENT',
        'CampaignReaction/0.1 (research; +https://github.com/666ghj/MiroFish)'
    )
    CORPUS_HTTP_TIMEOUT = float(os.environ.get('CORPUS_HTTP_TIMEOUT', '30'))
    CORPUS_RATE_PER_SECOND = float(os.environ.get('CORPUS_RATE_PER_SECOND', '1.0'))
    CORPUS_MAX_RESPONSE_BYTES = int(os.environ.get('CORPUS_MAX_RESPONSE_BYTES', str(16 * 1024 * 1024)))
    # Author handles are hashed with this salt and never stored raw. Changing it
    # invalidates existing pseudonyms, which is the intended way to rotate.
    CORPUS_AUTHOR_SALT = os.environ.get('CORPUS_AUTHOR_SALT', 'campaign-reaction-default-salt')

    # Reddit official OAuth API - free "script" app at reddit.com/prefs/apps
    REDDIT_CLIENT_ID = os.environ.get('REDDIT_CLIENT_ID')
    REDDIT_CLIENT_SECRET = os.environ.get('REDDIT_CLIENT_SECRET')

    # YouTube Data API v3 - free key, 10000 quota units per day
    YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')
    YOUTUBE_QUOTA_BUDGET = int(os.environ.get('YOUTUBE_QUOTA_BUDGET', '5000'))

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
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未配置")
        if os.environ.get("ZEP_API_URL"):
            errors.append("ZEP_API_URL 不受支持；MiroFish 仅连接 Zep Cloud")
        if cls.DEBUG:
            import warnings
            warnings.warn("Flask DEBUG mode is enabled. Do not use in production.", RuntimeWarning)
        return errors
