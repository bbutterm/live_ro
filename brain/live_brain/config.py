"""Настройки из env-файла лаборатории ($LAB_ROOT/secrets/live_ro.env)."""
from dataclasses import dataclass
from pathlib import Path
import json

UNSET = {"", "CHANGE_ME"}


def load_env(path):
    env = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def _int(env, key, default):
    value = env.get(key, "")
    return int(value) if value.isdigit() else default


@dataclass
class Settings:
    llm_provider: str
    api_key: str
    model: str
    api_base: str
    daily_limit: int
    decide_interval: int
    event_min_gap: int
    chat_min_gap: int
    timeout: int
    max_tokens: int

    @property
    def llm_enabled(self):
        # Платные вызовы только по явному согласию: BRAIN_LLM=openrouter и ключ.
        return self.llm_provider == "openrouter" and self.api_key not in UNSET

    @property
    def llm_off_reason(self):
        if self.llm_provider != "openrouter":
            return "LLM выключен (BRAIN_LLM=off)"
        if self.api_key in UNSET:
            return "нет OPENROUTER_API_KEY"
        return None

    @classmethod
    def from_env(cls, env):
        return cls(
            llm_provider=(env.get("BRAIN_LLM", "") or "off").lower(),
            api_key=env.get("OPENROUTER_API_KEY", ""),
            model=env.get("OPENROUTER_MODEL", "") or "deepseek/deepseek-chat",
            api_base=(env.get("BRAIN_API_BASE", "") or "https://openrouter.ai/api/v1").rstrip("/"),
            daily_limit=_int(env, "BRAIN_DAILY_LIMIT", 300),
            decide_interval=_int(env, "BRAIN_DECIDE_INTERVAL", 300),
            event_min_gap=_int(env, "BRAIN_EVENT_MIN_GAP", 60),
            chat_min_gap=_int(env, "BRAIN_CHAT_MIN_GAP", 15),
            timeout=_int(env, "BRAIN_TIMEOUT", 30),
            max_tokens=_int(env, "BRAIN_MAX_TOKENS", 500),
        )


def load_persona(path):
    persona = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("name", "character", "speech", "goals", "hunt_maps"):
        if key not in persona:
            raise ValueError(f"{path}: нет поля {key}")
    return persona
