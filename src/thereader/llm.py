"""Talk to whichever AI provider an API key belongs to.

Anthropic goes through its own SDK; every other provider through its OpenAI-compatible chat API, so adding one
is a line in PROVIDERS. On your own computer you can also point The Reader at any OpenAI-compatible server
(Ollama, LM Studio, a company gateway) with the "custom" provider.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

# Default models are chosen for clear, cheap explanations. Providers retire models, so when a default is gone
# the next best one is picked from the provider's own model list (see "prefer"), newest version first.
PROVIDERS: dict[str, dict] = {
    "openai": {"name": "OpenAI", "base": "https://api.openai.com/v1", "model": "gpt-6-luna",
               "prefer": [r"gpt-[\d.]+-luna", r"gpt-[\d.]+-mini", r"gpt-[\d.]+"], "env": ["OPENAI_API_KEY"]},
    "google": {"name": "Google Gemini", "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-flash-latest", "prefer": [r"gemini-flash-latest", r"gemini-[\d.]+-flash"],
               "env": ["GEMINI_API_KEY"]},
    "anthropic": {"name": "Anthropic", "base": None, "model": "claude-sonnet-5-5",
                  "prefer": [r"claude-sonnet-[\d-]+", r"claude-haiku-[\d-]+"], "env": ["ANTHROPIC_API_KEY"]},
    "groq": {"name": "Groq", "base": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
             "prefer": [r"llama-[\d.]+-70b-versatile", r"openai/gpt-oss-120b"], "env": ["GROQ_API_KEY"]},
    "mistral": {"name": "Mistral", "base": "https://api.mistral.ai/v1", "model": "mistral-medium-latest",
                "prefer": [r"mistral-medium-latest", r"mistral-small-latest", r"mistral-large-latest"],
                "env": ["MISTRAL_API_KEY"]},
    "deepseek": {"name": "DeepSeek", "base": "https://api.deepseek.com", "model": "deepseek-flash",
                 "prefer": [r"deepseek-flash", r"deepseek-chat", r"deepseek-v[\d.]+-flash"], "env": ["DEEPSEEK_API_KEY"]},
    "xai": {"name": "xAI", "base": "https://api.x.ai/v1", "model": "grok-latest",
            "prefer": [r"grok-latest", r"grok-[\d.]+"], "env": ["XAI_API_KEY"]},
    "openrouter": {"name": "OpenRouter", "base": "https://openrouter.ai/api/v1", "model": "openrouter/auto",
                   "prefer": [r"openrouter/auto"], "env": ["OPENROUTER_API_KEY"]},
}
CUSTOM = "custom"  # any OpenAI-compatible server; never allowed from visitors of a public server
TIMEOUT = 120

_picked: dict[str, str] = {}  # provider -> model chosen after its default was retired


class AIError(Exception):
    """A failed AI request, with a message for the page and an HTTP status for it."""

    def __init__(self, message: str, status: int = 502):
        message = message[:1].upper() + message[1:]
        super().__init__(message)
        self.status = status


@dataclass
class AIConfig:
    provider: str
    key: str
    model: str | None = None  # None: the provider's default
    base_url: str | None = None  # only for CUSTOM

    @property
    def name(self) -> str:
        return PROVIDERS[self.provider]["name"] if self.provider in PROVIDERS else "your AI server"

    def model_name(self) -> str:
        if self.model:
            return self.model
        return _picked.get(self.provider) or PROVIDERS.get(self.provider, {}).get("model", "")


def detect(key: str) -> str | None:
    """The provider a key belongs to, from its shape. None when it can't be told."""
    k = key.strip()
    if k.startswith("sk-ant-"):
        return "anthropic"
    if k.startswith("sk-or-"):
        return "openrouter"
    if k.startswith("AIza"):
        return "google"
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("xai-"):
        return "xai"
    if re.fullmatch(r"sk-[0-9a-f]{32}", k):
        return "deepseek"
    if k.startswith("sk-"):
        return "openai"
    if re.fullmatch(r"[A-Za-z0-9]{32}", k):
        return "mistral"
    return None


def server_config() -> AIConfig | None:
    """The server's own key, from THE_READER_AI_KEY (with THE_READER_AI_PROVIDER, THE_READER_MODEL and, for
    any OpenAI-compatible server, THE_READER_AI_BASE_URL) or from a provider's usual variable."""
    key = os.environ.get("THE_READER_AI_KEY", "").strip()
    provider = os.environ.get("THE_READER_AI_PROVIDER", "").strip().lower() or None
    base = os.environ.get("THE_READER_AI_BASE_URL", "").strip() or None
    model = os.environ.get("THE_READER_MODEL", "").strip() or None
    if not key:
        order = ["anthropic"] + [p for p in PROVIDERS if p != "anthropic"]  # ANTHROPIC_API_KEY came first in 0.4
        for pid in ([provider] if provider in PROVIDERS else order):
            found = next((os.environ[e].strip() for e in PROVIDERS[pid]["env"] if os.environ.get(e, "").strip()), "")
            if found:
                key, provider = found, pid
                break
    if base:
        provider = CUSTOM
    if not key and provider != CUSTOM:
        return None
    provider = provider or detect(key)
    if provider != CUSTOM and provider not in PROVIDERS:
        return None
    return AIConfig(provider, key, model, base)


def describe() -> list[dict]:
    """Providers for the key settings on the page."""
    return [{"id": pid, "name": p["name"], "model": _picked.get(pid) or p["model"]} for pid, p in PROVIDERS.items()]


# ---------------------------------------------------------------- asking
def complete(cfg: AIConfig, system: str, prompt: str, max_tokens: int = 1100) -> str:
    """The model's answer to `prompt`. Raises AIError with a message meant for the person reading."""
    if cfg.provider == "anthropic":
        return _with_fallback(cfg, lambda model: _anthropic(cfg, model, system, prompt, max_tokens))
    base = (cfg.base_url or "").rstrip("/") if cfg.provider == CUSTOM else PROVIDERS[cfg.provider]["base"]
    if cfg.provider == CUSTOM and not cfg.model:
        raise AIError("Type the model name for your AI server in the key settings.", 400)
    return _with_fallback(cfg, lambda model: _chat(cfg, base, model, system, prompt))


class _ModelMissing(Exception):
    pass


def _with_fallback(cfg: AIConfig, ask) -> str:
    model = cfg.model_name()
    try:
        return ask(model)
    except _ModelMissing:
        if cfg.model or cfg.provider == CUSTOM:  # a model the person chose: tell them, don't swap it
            raise AIError(f"{cfg.name} doesn't offer the model “{model}” to this key. Check the model name in the "
                          "key settings, or clear it to use the default.", 400) from None
        better = _replacement(cfg, model)
        if not better:
            raise AIError(f"{cfg.name} no longer offers the model The Reader uses by default. Type a model name "
                          "in the key settings.", 400) from None
        _picked[cfg.provider] = better
        try:
            return ask(better)
        except _ModelMissing:
            raise AIError(f"{cfg.name} didn't accept the model “{better}”. Type a model name in the key settings.",
                          400) from None


def _version(model_id: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", model_id))


def _replacement(cfg: AIConfig, missing: str) -> str | None:
    """The best model the provider still offers this key, by the provider's preference list."""
    try:
        ids = _list_models(cfg)
    except AIError:
        return None
    for pattern in PROVIDERS[cfg.provider]["prefer"]:
        hits = [i for i in ids if re.fullmatch(pattern, i) and i != missing]
        if hits:
            return max(hits, key=_version)
    return None


def _list_models(cfg: AIConfig) -> list[str]:
    if cfg.provider == "anthropic":
        import anthropic
        try:
            return [m.id for m in anthropic.Anthropic(api_key=cfg.key).models.list(limit=100)]
        except Exception as e:
            raise AIError(str(e)) from None
    data = _request(cfg, f"{PROVIDERS[cfg.provider]['base']}/models", None)
    return [str(m.get("id", "")).removeprefix("models/") for m in data.get("data", [])]


def _chat(cfg: AIConfig, base: str, model: str, system: str, prompt: str) -> str:
    # No token limit: reasoning models count their thinking against it, and the prompts already ask for short
    # answers. Every provider here accepts this minimal request.
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    data = _request(cfg, f"{base}/chat/completions", body)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError(f"{cfg.name} sent back an answer The Reader couldn't read. Try again.") from None
    if isinstance(content, list):  # some servers send a list of parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = (content or "").strip()
    if not text:
        raise AIError(f"{cfg.name} sent back an empty answer. Try again, or pick another model in the key settings.")
    return text


def _request(cfg: AIConfig, url: str, body: dict | None) -> dict:
    headers = {"Authorization": f"Bearer {cfg.key or 'none'}", "Content-Type": "application/json",
               "User-Agent": "the-reader"}
    if cfg.provider == "openrouter":
        headers["X-Title"] = "The Reader"
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers,
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = _error_detail(e)
        if e.code == 404 or (e.code == 400 and _MISSING_MODEL.search(detail)):
            raise _ModelMissing() from None
        raise _status_error(cfg, e.code, detail) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AIError(f"Couldn't reach {cfg.name}. Try again in a moment.") from None
    except ValueError:
        raise AIError(f"{cfg.name} sent back an answer The Reader couldn't read. Try again.") from None


# how providers that answer 400 instead of 404 say a model doesn't exist ("Invalid model", "Model Not Exist",
# "x is not a valid model ID")
_MISSING_MODEL = re.compile(r"invalid model|unknown model|model\b.*\b(not exist|does not exist|not found|not a valid|"
                            r"not available|no such)|not a valid model", re.I)


def _error_detail(e: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(e.read().decode("utf-8", "replace"))
    except (ValueError, OSError):
        return ""
    err = data[0] if isinstance(data, list) and data else data
    err = err.get("error", err) if isinstance(err, dict) else err
    msg = err.get("message") if isinstance(err, dict) else err
    return str(msg or "")[:300]


def _status_error(cfg: AIConfig, status: int, detail: str = "") -> AIError:
    if status in (401, 403):
        return AIError(f"{cfg.name} didn't accept this API key. Check that it's copied correctly and still active.", 401)
    if status == 402:
        return AIError(f"Your {cfg.name} account is out of credit.", 402)
    if status == 429:
        return AIError(f"{cfg.name} is limiting requests from this key right now. Wait a minute and try again.", 429)
    if status >= 500:
        return AIError(f"{cfg.name} is having trouble right now. Try again in a moment.")
    print(f"the-reader: {cfg.name} answered {status}: {detail}", file=sys.stderr)
    return AIError(f"{cfg.name} couldn't write this explanation" + (f": {detail}" if detail else ". Try again."))


def _anthropic(cfg: AIConfig, model: str, system: str, prompt: str, max_tokens: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=cfg.key, timeout=TIMEOUT, max_retries=1)
    try:
        msg = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                     messages=[{"role": "user", "content": prompt}])
    except anthropic.NotFoundError:
        raise _ModelMissing() from None
    except anthropic.APIStatusError as e:
        raise _status_error(cfg, e.status_code, getattr(e, "message", "")) from None
    except anthropic.APIConnectionError:
        raise AIError(f"Couldn't reach {cfg.name}. Try again in a moment.") from None
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise AIError(f"{cfg.name} sent back an empty answer. Try again.")
    return text
