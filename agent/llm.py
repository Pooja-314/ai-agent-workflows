"""Thin LLM wrapper.

* Provider is picked from the environment (Anthropic or OpenAI). 
* Every call site passes an `offline` function. If no key is configured, or the
  API call / JSON parsing fails, the offline function is used instead, and the
  returned `source` says so. The whole project therefore runs end-to-end with
  no API key, and the trace always states honestly which path produced the text.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from . import config

_DEFAULT_MODELS = {"anthropic": "claude-sonnet-4-5", "openai": "gpt-4o-mini", "ollama": "mistral"}


def provider() -> str:
    p = config.LLM_PROVIDER
    if p == "offline":
        return "offline"
    if p in ("anthropic", "auto") and config.ANTHROPIC_API_KEY:
        return "anthropic"
    if p in ("openai", "auto") and config.OPENAI_API_KEY:
        return "openai"
    if p == "ollama":
        return "ollama"
    return "offline"


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in LLM reply")
    return json.loads(text[start:end + 1])


def _call(system: str, user: str) -> str:
    prov = provider()
    model = config.LLM_MODEL or _DEFAULT_MODELS[prov]
    if prov == "ollama":
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
        }).encode("utf-8")
        req = urllib.request.Request(
            config.OLLAMA_BASE_URL.rstrip("/") + "/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return body.get("message", {}).get("content", "")
    if prov == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(model=model, max_tokens=2000, system=system,
                                     messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    import openai
    client = openai.OpenAI(api_key=config.OPENAI_API_KEY)
    r = client.chat.completions.create(
        model=model, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    return r.choices[0].message.content or ""


def complete_json(system: str, user: str, offline: Callable[[], dict],
                  validate: Callable[[dict], None] | None = None) -> tuple[dict, str]:
    """Return (data, source). source is 'llm:<provider>' or 'offline-simulated[...]'."""
    prov = provider()
    if prov == "offline":
        return offline(), "offline-simulated"
    try:
        data = _extract_json(_call(system + "\nReply with a single JSON object only.", user))
        if validate:
            validate(data)
        return data, f"llm:{prov}"
    except Exception as e:  # network, auth, bad JSON, failed validation
        return offline(), f"offline-simulated (LLM failed: {type(e).__name__}: {str(e)[:80]})"
