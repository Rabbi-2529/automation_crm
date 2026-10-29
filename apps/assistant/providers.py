"""AI providers. Everything speaks the OpenAI chat-completions format, which Ollama, Groq, OpenRouter, LM Studio,
vLLM, Together and OpenAI all accept, so switching AI later is a settings change, not a code change.
"""

import re
from dataclasses import dataclass

import requests
from django.conf import settings

TIMEOUT = 90  # open-source models on a CPU-only server can be slow
THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)  # reasoning models (DeepSeek-R1, Qwen3) think out loud


@dataclass(frozen=True)
class Preset:
    label: str
    base_url: str = ""
    model: str = ""
    needs_key: bool = False
    note: str = ""


BUILTIN = "builtin"
PRESETS = {
    BUILTIN: Preset("Built-in help only (no AI)", note="Answers come from the help center articles. Nothing leaves your server."),
    "ollama": Preset("Ollama: open-source models on your own server (free)", "http://localhost:11434/v1", "llama3.2:3b",
                     note="Install Ollama, run `ollama pull llama3.2:3b`. Data stays on your server."),
    "groq": Preset("Groq: hosted open-source Llama models (free tier)", "https://api.groq.com/openai/v1", "llama-3.1-8b-instant",
                   needs_key=True, note="Free API key at console.groq.com. Questions are sent to Groq."),
    "openrouter": Preset("OpenRouter: many open-source models (free ones available)", "https://openrouter.ai/api/v1",
                         "meta-llama/llama-3.3-70b-instruct:free", needs_key=True,
                         note="Free API key at openrouter.ai; model names ending in :free cost nothing."),
    "openai_compatible": Preset("Other OpenAI-compatible API (OpenAI, LM Studio, vLLM, Together…)", needs_key=False,
                                note="Paste the API address (ending in /v1), model name and key if it needs one."),
}


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = 0.3
    source: str = "platform"  # "platform" (AI_* settings) or "workspace"

    @property
    def is_ai(self):
        return self.provider != BUILTIN and bool(self.base_url and self.model)

    @property
    def label(self):
        preset = PRESETS.get(self.provider)
        return f"{preset.label.split(':')[0] if preset else self.provider} · {self.model}" if self.is_ai else "Built-in help"


def resolve(org):
    """The AI config for a workspace: its own choice, else the platform default from settings/env."""
    from .models import AssistantSettings

    own = AssistantSettings.for_org(org)
    if own.provider:
        preset = PRESETS.get(own.provider, PRESETS["openai_compatible"])
        return Config(own.provider, (own.base_url or preset.base_url).rstrip("/"), own.model or preset.model, own.api_key,
                      float(own.temperature), "workspace")
    provider = settings.AI_PROVIDER or BUILTIN
    preset = PRESETS.get(provider, PRESETS["openai_compatible"])
    return Config(provider, (settings.AI_BASE_URL or preset.base_url).rstrip("/"), settings.AI_MODEL or preset.model,
                  settings.AI_API_KEY, float(own.temperature), "platform")


def chat(config, messages, max_tokens=700):
    """Sends OpenAI-style messages and returns the reply text."""
    if not config.is_ai:
        raise ProviderError("No AI model is configured.")
    headers = {"Content-Type": "application/json", "X-Title": "ThingBD CRM"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    payload = {"model": config.model, "messages": messages, "temperature": config.temperature, "max_tokens": max_tokens,
               "stream": False}
    try:
        response = requests.post(f"{config.base_url}/chat/completions", json=payload, headers=headers, timeout=TIMEOUT)
    except requests.Timeout as exc:
        raise ProviderError(f"The AI at {config.base_url} took too long to answer.") from exc
    except requests.RequestException as exc:
        hint = " Is Ollama running?" if config.provider == "ollama" else ""
        raise ProviderError(f"Can't reach the AI at {config.base_url}.{hint}") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        error = data.get("error")
        message = (error.get("message") if isinstance(error, dict) else error) or response.text[:200]
        raise ProviderError(f"AI error {response.status_code}: {message}")
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("The AI sent back an answer in an unexpected format.") from exc
    text = THINK.sub("", text).strip()
    if not text:
        raise ProviderError("The AI sent back an empty answer.")
    return text
