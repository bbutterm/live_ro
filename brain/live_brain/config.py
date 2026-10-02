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
class Provider:
    """OpenAI-совместимый провайдер (те же поля читает llm.chat)."""
    name: str
    api_base: str
    api_key: str
    model: str
    timeout: int
    max_tokens: int
    daily_limit: int

    @property
    def ready(self):
        return self.api_key not in UNSET and bool(self.model) and bool(self.api_base)


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
    gate: str
    safe_hp: int
    jev: Provider
    peer_replies_per_hour: int
    peer_smalltalk_every: int

    @property
    def llm_enabled(self):
        # Платные вызовы только по явному согласию: BRAIN_LLM=openrouter и ключ.
        return self.llm_provider == "openrouter" and self.api_key not in UNSET

    @property
    def fast_enabled(self):
        # JEV — быстрый gate: только при BRAIN_GATE=jev и заполненных JEV_*.
        return self.gate == "jev" and self.jev.ready

    @property
    def llm_off_reason(self):
        if self.llm_provider != "openrouter":
            return "BRAIN_LLM=off, платные вызовы не включены"
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
            gate=(env.get("BRAIN_GATE", "") or "rules").lower(),
            safe_hp=_int(env, "BRAIN_SAFE_HP", 30),
            jev=Provider(
                name="jev",
                api_base=env.get("JEV_API_BASE", "").rstrip("/"),
                api_key=env.get("JEV_API_KEY", ""),
                model=env.get("JEV_MODEL", ""),
                timeout=_int(env, "JEV_TIMEOUT", 5),
                max_tokens=_int(env, "JEV_MAX_TOKENS", 200),
                daily_limit=_int(env, "JEV_DAILY_LIMIT", 2000),
            ),
            peer_replies_per_hour=_int(env, "BRAIN_PEER_REPLIES_PER_HOUR", 6),
            peer_smalltalk_every=_int(env, "BRAIN_PEER_SMALLTALK", 1800),
        )


def load_persona(path):
    persona = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("name", "character", "speech", "goals", "hunt_maps"):
        if key not in persona:
            raise ValueError(f"{path}: нет поля {key}")
    return persona
