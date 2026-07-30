"""
LLM client wrapper.
All providers are called through the OpenAI format.
"""

import json
import logging
import re
import time
from typing import Optional, Dict, Any, List
from openai import OpenAI

from ..config import Config
from .openai_chat_compat import create_chat_completion, extract_chat_completion_text
from .pipeline_logger import current_llm_caller, pipeline_log


logger = logging.getLogger(__name__)


class LLMResponseError(ValueError):
    """A safe, structured error for unusable model responses."""

    def __init__(self, message: str, *, finish_reason: Optional[str] = None):
        super().__init__(message)
        self.finish_reason = finish_reason


def _is_response_format_unsupported(error: Exception) -> bool:
    """Detect an explicit provider rejection of JSON response_format."""

    if getattr(error, "status_code", None) not in {400, 422}:
        return False

    body = getattr(error, "body", None)
    details = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(details, str):
        # Not every OpenAI-compatible server sends the {"error": {"param": ...}}
        # object. LM Studio answers with a bare string - "'response_format.type'
        # must be 'json_schema' or 'text'" - either as the whole body or under
        # an "error" key.
        return "response_format" in details.lower()
    if not isinstance(details, dict):
        return False

    param = str(details.get("param") or "").strip().lower()
    if param == "response_format" or param.startswith("response_format."):
        return True

    # A 400/422 that names response_format at all is a rejection of the
    # parameter. Matching on phrasing instead ("not support", "unsupported",
    # ...) only works for the wordings that have already been seen, and every
    # provider words it differently.
    return "response_format" in str(details.get("message") or "").lower()


def _clean_chat_text(content: str) -> str:
    """Remove common reasoning wrappers and an outer Markdown JSON fence."""

    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
    cleaned = cleaned.lstrip("\ufeff")
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    return cleaned.strip()


def _extract_usage(response: Any) -> Dict[str, Any]:
    """Pull the token counts off a completion, when the provider reports them."""

    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: getattr(usage, key, None)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


def _contains_additional_json_container(content: str) -> bool:
    """Return True when trailing text embeds another JSON object or array."""

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", content):
        try:
            value, _ = decoder.raw_decode(content[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return True
    return False


class LLMClient:
    """LLM client."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError(f"LLM_API_KEY is not configured (base_url={self.base_url})")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    @classmethod
    def for_chatbot(cls) -> "LLMClient":
        """Client for the interactive chatbot, which may run a different model."""
        return cls(
            api_key=Config.CHATBOT_LLM_API_KEY,
            base_url=Config.CHATBOT_LLM_BASE_URL,
            model=Config.CHATBOT_LLM_MODEL_NAME,
        )

    @classmethod
    def for_vision(cls) -> "LLMClient":
        """Client for reading page images, which needs a model that can see."""
        return cls(
            api_key=Config.VISION_LLM_API_KEY,
            base_url=Config.VISION_LLM_BASE_URL,
            model=Config.VISION_LLM_MODEL_NAME,
        )

    def _create_completion(
        self,
        *,
        messages: List[Dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        response_format: Optional[Dict[str, Any]],
        call_kind: str = "chat",
        attempt: int = 1,
    ) -> Any:
        """Send one raw Chat Completions request through the compatibility layer."""

        component, target = current_llm_caller()
        params = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
        }
        started = time.perf_counter()

        try:
            response = create_chat_completion(
                self.client,
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except Exception as error:
            pipeline_log.llm_call(
                component or "LLMClient",
                f"llm.{call_kind}",
                model=self.model,
                messages=messages,
                params=params,
                response_text=None,
                duration_ms=(time.perf_counter() - started) * 1000,
                status="error",
                target=target,
                attempts=attempt,
                error=f"{type(error).__name__}: {error}",
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        finish_reason = None
        choices = getattr(response, "choices", None) or []
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)

        pipeline_log.llm_call(
            component or "LLMClient",
            f"llm.{call_kind}",
            model=self.model,
            messages=messages,
            params=params,
            response_text=extract_chat_completion_text(response),
            duration_ms=duration_ms,
            target=target,
            usage=_extract_usage(response),
            attempts=attempt,
            extra_metrics={"finish_reason": finish_reason},
        )
        return response
    
    def chat(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        Send a chat request.

        Args:
            messages: Message list
            temperature: Sampling temperature
            max_tokens: Maximum number of tokens
            response_format: Response format (e.g. JSON mode)

        Returns:
            The model response text
        """
        response = self._create_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        content = extract_chat_completion_text(response)
        return _clean_chat_text(content)
    
    def chat_json(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: Optional[int] = 4096,
        max_attempts: int = 1,
    ) -> Dict[str, Any]:
        """
        Send a chat request and return parsed JSON.

        Args:
            messages: Message list
            temperature: Sampling temperature
            max_tokens: Maximum number of tokens
            max_attempts: Content-generation attempts (excluding the one explicit
                JSON-mode capability downgrade)

        Returns:
            The parsed JSON object
        """
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        response_format: Optional[Dict[str, str]] = {"type": "json_object"}
        request_max_tokens = max_tokens
        last_error: Optional[LLMResponseError] = None

        for attempt in range(1, max_attempts + 1):
            # JSON-mode capability negotiation is separate from content
            # regeneration. An explicit response_format rejection may add one
            # request, but it must not consume a content attempt.
            while True:
                try:
                    response = self._create_completion(
                        messages=messages,
                        temperature=temperature,
                        max_tokens=request_max_tokens,
                        response_format=response_format,
                        call_kind="chat_json",
                        attempt=attempt,
                    )
                except Exception as error:
                    if (
                        response_format is not None
                        and _is_response_format_unsupported(error)
                    ):
                        logger.warning(
                            "LLM provider explicitly rejected response_format; "
                            "retrying once with prompt-only JSON guidance"
                        )
                        component, target = current_llm_caller()
                        pipeline_log.action(
                            component or "LLMClient",
                            "llm.json_mode_downgrade",
                            status="warn",
                            target=target,
                            metrics={"attempt": attempt},
                        )
                        response_format = None
                        continue
                    raise
                break

            try:
                return self._parse_json_response(response)
            except LLMResponseError as error:
                last_error = error
                component, target = current_llm_caller()
                pipeline_log.action(
                    component or "LLMClient",
                    "llm.json_parse_failed",
                    status="error" if attempt >= max_attempts else "warn",
                    target=target,
                    metrics={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "finish_reason": error.finish_reason,
                    },
                    error=str(error),
                )
                if attempt >= max_attempts:
                    raise

                # A caller-supplied cap is the common cause of a partial JSON
                # object. Omit it for the one bounded retry so the provider can
                # use its model-specific output limit.
                had_token_cap = request_max_tokens is not None
                request_max_tokens = None
                logger.warning(
                    "LLM returned unusable JSON (finish_reason=%s); "
                    "retrying content generation%s",
                    error.finish_reason or "unknown",
                    " without an output token cap" if had_token_cap else "",
                )

        if last_error is not None:  # pragma: no cover - defensive loop guard
            raise last_error
        raise LLMResponseError("LLM did not produce a JSON response")

    @staticmethod
    def _parse_json_response(response: Any) -> Dict[str, Any]:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMResponseError("LLM returned no choices")

        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise LLMResponseError(
                "LLM JSON output was truncated at the token limit",
                finish_reason=finish_reason,
            )
        if finish_reason not in {None, "stop"}:
            raise LLMResponseError(
                f"LLM JSON generation stopped unexpectedly ({finish_reason})",
                finish_reason=finish_reason,
            )

        content = _clean_chat_text(extract_chat_completion_text(response))
        if not content:
            raise LLMResponseError(
                "LLM returned empty JSON content",
                finish_reason=finish_reason,
            )

        try:
            value = json.loads(content)
        except json.JSONDecodeError as strict_error:
            # Some compatible providers append a short explanation after an
            # otherwise complete JSON object. Accept only an object decoded
            # from the beginning; never repair or invent truncated JSON.
            try:
                value, end = json.JSONDecoder().raw_decode(content)
            except json.JSONDecodeError:
                raise LLMResponseError(
                    "LLM returned invalid JSON "
                    f"(line {strict_error.lineno}, column {strict_error.colno})",
                    finish_reason=finish_reason,
                ) from strict_error

            trailing = content[end:].strip()
            if trailing:
                if _contains_additional_json_container(trailing):
                    raise LLMResponseError(
                        "LLM returned multiple JSON values",
                        finish_reason=finish_reason,
                    )
                logger.warning("Ignoring text after a complete LLM JSON object")

        if not isinstance(value, dict):
            raise LLMResponseError(
                "LLM JSON response must be a top-level JSON object",
                finish_reason=finish_reason,
            )

        return value
