"""Явная география синтетических сценариев Пронтеры, не настройки лаборатории.

Характер/реплики берём из persona, но карты сценария фиксируем независимо от
места текущего runtime. География совпадает с upstream 579b0ef: поля Пронтеры,
без persona.routine.town (город выбирается из world/home как в исходных тестах).
Каждый вызов возвращает новый объект; production JSON никогда не изменяется.
Проверки реальных deployment persona должны читать их напрямую.
"""
import json
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parents[1]
PRONTERA_HUNT_MAPS = ("prt_fild08", "prt_fild07", "prt_fild05")


def prontera_persona(bot="bot01"):
    persona = json.loads((BRAIN_DIR / "personas" / f"{bot}.json").read_text(encoding="utf-8"))
    persona["hunt_maps"] = list(PRONTERA_HUNT_MAPS)
    persona.setdefault("routine", {}).pop("town", None)
    return persona


def write_prontera_personas(root):
    """CLI нужен собственный каталог persona (peer_names ищет соседние JSON)."""
    folder = Path(root) / "personas"
    folder.mkdir(parents=True, exist_ok=True)
    for bot in ("bot01", "bot02"):
        (folder / f"{bot}.json").write_text(json.dumps(prontera_persona(bot), ensure_ascii=False), encoding="utf-8")
    return folder / "bot01.json"
