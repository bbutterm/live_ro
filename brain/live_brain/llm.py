"""Клиент OpenRouter (OpenAI-совместимый chat/completions) на стандартной библиотеке.

Ключ берётся только из настроек и никогда не пишется в логи.
"""
import json
import re
import time
import urllib.error
import urllib.request


class LLMError(Exception):
    pass


def chat(settings, messages, json_mode=True, max_tokens=None):
    """Возвращает (text, usage, latency). Блокирующий вызов — запускать в executor."""
    body = {
        "model": settings.model,
        "messages": messages,
        "max_tokens": max_tokens or settings.max_tokens,
        "temperature": 0.8,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        settings.api_base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.api_key}",
            "Content-Type": "application/json",
            "X-Title": "live_ro",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=settings.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise LLMError(f"HTTP {e.code}: {redact(detail, settings.api_key)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"сеть: {redact(str(e), settings.api_key)}") from None
    except json.JSONDecodeError:
        raise LLMError("ответ не JSON") from None
    latency = time.monotonic() - started
    if "error" in payload:
        raise LLMError(f"API: {redact(json.dumps(payload['error'], ensure_ascii=False)[:300], settings.api_key)}")
    try:
        text = payload["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("в ответе нет choices[0].message.content") from None
    return text, payload.get("usage") or {}, latency


def redact(text, key):
    if key and len(key) > 6:
        text = text.replace(key, "***")
    return re.sub(r"sk-or-[A-Za-z0-9_-]{8,}", "sk-or-***", text)


def parse_json_object(text):
    """Достаёт первый JSON-объект из ответа модели (бывает в ```json ... ```)."""
    text = text.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    raise LLMError("модель вернула не JSON-объект")
