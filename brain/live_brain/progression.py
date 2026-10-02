"""Рост жителя: цели по уровню, смена профессии, снаряжение (AUT-049..054, 079..084). Правила без LLM.

Данные: brain/world/progression.json (сценарии Swordman -> Knight, Acolyte -> Priest, выведенные из
скриптов rAthena со ссылками file:line) и brain/world/jobs/catalog.json (магазины и добыча, генерирует
scripts/gen_progression.py). Все функции чистые: принимают state (как у brainBridge) и данные.

state: job (имя OpenKore: Swordsman, Acolyte…), lv, job_lv, zeny, sex, items {id: n};
    необязательно: skill_points, quests [id] (журнал квестов), equip {слот: id},
    job_change {quests, skill_points, items} — статус плагина jobChange (если его добавят в state).
Чего нет в state — «неизвестно», не «выполнено» и не «не выполнено».

Цель (plan): этапы пути; следующий шаг — первый незавершённый; срок годности (expires) — по виду
шага из goals.ttl_hours; unreachable — нужен предмет, который нигде не продаётся и не падает
с монстров, появляющихся на картах. «Трудный» предмет (все монстры выше уровня + hard_mob_level_gap)
не недостижим, но для Knight при трудных наборах выгоднее job 50: Sir Andrew тогда не просит
предметов (knight.txt:547), и решить это нужно ДО разговора с ним — после rand набор закреплён.
"""
import json
import time
from pathlib import Path

WORLD = Path(__file__).resolve().parents[1] / "world"
SLOT_LOCATIONS = {"weapon": ("Right_Hand", "Both_Hand"), "Armor": ("Armor",), "Left_Hand": ("Left_Hand",),
                  "Shoes": ("Shoes",), "Garment": ("Garment",), "Head_Top": ("Head_Top",)}


def load(world_dir=None):
    world = Path(world_dir) if world_dir else WORLD
    data = json.loads((world / "progression.json").read_text(encoding="utf-8"))
    cat = world / "jobs" / "catalog.json"
    data["catalog"] = json.loads(cat.read_text(encoding="utf-8")) if cat.exists() else {"equipment": [], "items": {}}
    return data


# ---------- состояние ----------

def _job_change(state):
    return state.get("job_change") or {}


def quest_ids(state):
    """Активные квесты из state; None — журнал неизвестен."""
    q = state.get("quests")
    if q is None:
        q = _job_change(state).get("quests")
    if q is None:
        return None
    return {int(x) for x in (q.keys() if isinstance(q, dict) else q)}


def skill_points(state):
    v = state.get("skill_points")
    if v is None:
        v = _job_change(state).get("skill_points")
    return None if v is None else int(v)


def items(state):
    out = {str(k): int(v or 0) for k, v in (state.get("items") or {}).items()}
    for k, v in (_job_change(state).get("items") or {}).items():
        out[str(k)] = max(out.get(str(k), 0), int(v or 0))
    return out


def path_for(state, data):
    cls = data["classes"].get(state.get("job") or "")
    return cls and cls.get("next")


# ---------- источники предметов ----------

def item_sources(item_id, data, lv=None):
    """Где взять предмет: магазины и монстры. status: shop | drop | hard | unreachable | unknown."""
    info = data["catalog"]["items"].get(str(item_id))
    if info is None:
        return {"id": int(item_id), "name": None, "status": "unknown", "shops": [], "drops": []}
    drops = [d for d in info.get("drops", []) if d.get("maps")]
    out = {"id": int(item_id), "name": info.get("name"), "shops": info.get("shops", []), "drops": drops}
    if out["shops"]:
        out["status"] = "shop"
    elif not drops:
        out["status"] = "unreachable"
    else:
        gap = data["goals"]["hard_mob_level_gap"]
        easy = [d for d in drops if lv is None or d["level"] <= lv + gap]
        out["status"] = "drop" if easy else "hard"
        best = min(easy or drops, key=lambda d: (d["level"], -d["rate_pct"]))
        out["best"] = {"mob": best["mob"], "level": best["level"], "rate_pct": best["rate_pct"],
                       "map": next(iter(best["maps"]))}
    return out


def farm_maps(item_id, data, lv, atlas=None, top=3):
    """Где добывать предмет: карты, где появляются монстры-источники. С атласом (live_brain.atlas)
    — по риску карты для уровня lv (danger_for; карты вне атласа — по уровню монстра), без него — по
    уровню монстра и шансу."""
    out = []
    for d in item_sources(item_id, data, lv)["drops"]:
        for m, n in d["maps"].items():
            risk, why = atlas.danger_for(m, lv) if atlas else (None, [])
            if atlas and m not in atlas.maps:          # карты нет в атласе — оценка по уровню монстра
                risk, why = round(min(1.0, max(0.0, (d["level"] - lv + 5) / 20)), 2), [f"карты {m} нет в атласе"]
            out.append({"map": m, "mob": d["mob"], "level": d["level"], "rate_pct": d["rate_pct"], "count": n,
                        "risk": risk, "why": why})
    out.sort(key=lambda x: (0.5 if x["risk"] is None else x["risk"], x["level"], -x["rate_pct"] * x["count"]))
    return out[:top]


def item_set(path_data, state):
    """Набор Sir Andrew по журналу квестов: ('A'|'B', набор) или (None, None) — ещё не выбран rand."""
    sets = path_data.get("item_sets") or {}
    q = quest_ids(state) or set()
    for key in ("A", "B"):
        if key in sets and sets[key]["quest"] in q:
            return key, sets[key]
    return None, None


# ---------- этапы смены профессии ----------

def _when(stage, state, q):
    w = stage.get("when") or {}
    if "quests_none" in w and q & set(w["quests_none"]):
        return False
    if "quests_any" in w and not q & set(w["quests_any"]):
        return False
    if "map" in w and state.get("map") != w["map"]:
        return False
    return True


def current_stage(state, data, path=None, done=()):
    """Этап квеста, который делать сейчас (по журналу квестов). done — этапы, пройденные по памяти мозга
    (паломничество Priest не меняет журнал). Без журнала — первый этап (apply) как предположение."""
    path = path or path_for(state, data)
    if not path:
        return None
    q = quest_ids(state)
    for stage in data["paths"][path]["stages"]:
        if stage["id"] in done:
            continue
        if q is None:
            return stage
        if _when(stage, state, q):
            return stage
    return None


def readiness(state, data, path=None):
    """Готовность к смене профессии: missing (точно не хватает), unknown (нет в state), unreachable."""
    path = path or path_for(state, data)
    if not path:
        return {"path": None, "ready": False, "missing": [], "unknown": [], "unreachable": []}
    p = data["paths"][path]
    req = p["requirements"]
    missing, unknown, unreachable = [], [], []
    job_lv = int(state.get("job_lv") or 0)
    if job_lv < req["job_lv"]:
        missing.append({"kind": "job_lv", "need": req["job_lv"], "have": job_lv, "ref": req["refs"][0]})
    sp = skill_points(state)
    if sp is None:
        unknown.append({"kind": "skill_points", "need": 0, "why": "в state нет skill_points"})
    elif sp > req["skill_points"]:
        missing.append({"kind": "skill_points", "need": 0, "have": sp, "ref": req["refs"][1]})
    if req.get("zeny") and int(state.get("zeny") or 0) < req["zeny"]:
        missing.append({"kind": "zeny", "need": req["zeny"], "have": int(state.get("zeny") or 0)})
    if p.get("item_sets"):
        skip = p.get("skip") or {}
        q = quest_ids(state) or set()
        key, chosen = item_set(p, state)
        past_items = bool(q & set(p["item_sets"].get("after_quests", ())))
        if chosen:
            have = items(state)
            for iid, n in chosen["items"].items():
                if have.get(iid, 0) < n:
                    src = item_sources(iid, data, state.get("lv"))
                    entry = {"kind": "item", "id": int(iid), "name": src["name"], "need": n,
                             "have": have.get(iid, 0), "set": key, "source": src["status"], "best": src.get("best")}
                    missing.append(entry)
                    if src["status"] == "unreachable":
                        unreachable.append(entry)
        elif not past_items and job_lv < skip.get("job_lv", 999):
            unknown.append({"kind": "item_set", "why": p["item_sets"]["chosen_by"],
                            "sets": {k: v["items"] for k, v in p["item_sets"].items() if k in ("A", "B")},
                            "or": f"job {skip.get('job_lv')} — без предметов ({skip.get('ref')})"})
    return {"path": path, "to": p["to"], "ready": not missing and not unknown, "missing": missing,
            "unknown": unknown, "unreachable": unreachable}


def set_difficulty(path_data, data, lv):
    """Для каждого набора: статусы предметов. hard/unreachable в наборе -> набор трудный."""
    out = {}
    for key in ("A", "B"):
        s = (path_data.get("item_sets") or {}).get(key)
        if s:
            st = {iid: item_sources(iid, data, lv)["status"] for iid in s["items"]}
            out[key] = {"items": st, "hard": any(v in ("hard", "unreachable") for v in st.values()),
                        "unreachable": [int(i) for i, v in st.items() if v == "unreachable"]}
    return out


# ---------- цель ----------

def _goal_step(kind, text, data, now, **extra):
    ttl = data["goals"]["ttl_hours"].get(kind, data["goals"]["ttl_hours"]["quest_stage"])
    return dict(kind=kind, text=text, expires=now + ttl * 3600, **extra)


def plan(state, data, now=None, done=()):
    """Цель жителя: этапы и следующий шаг. Чистая функция; now — для срока годности."""
    now = now if now is not None else time.time()
    job = state.get("job")
    cls = data["classes"].get(job or "")
    lv, job_lv = int(state.get("lv") or 0), int(state.get("job_lv") or 0)
    if not cls:
        return {"job": job, "path": None, "stages": [], "unreachable": False,
                "next": _goal_step("base_lv", f"профессия {job!r} не описана в progression.json — просто расти", data, now)}
    path = cls.get("next")
    if not path:
        nxt = (_goal_step("job_lv", f"добрать уровень профессии до {cls['max_job']} (сейчас {job_lv})", data, now,
                          target=cls["max_job"], have=job_lv) if job_lv < cls["max_job"] else
               _goal_step("base_lv", f"поднимать базовый уровень (сейчас {lv})", data, now,
                          target=data["goals"]["base_lv_after_job"], have=lv))
        return {"job": job, "path": None, "stages": [], "unreachable": False, "next": nxt}

    p = data["paths"][path]
    ready = readiness(state, data, path)
    skip_lv = (p.get("skip") or {}).get("job_lv")
    stages = []
    need_job = p["requirements"]["job_lv"]
    stages.append({"id": "job_lv", "title": f"уровень профессии {need_job}", "done": job_lv >= need_job})
    diff = set_difficulty(p, data, lv) if p.get("item_sets") else {}
    q = quest_ids(state) or set()
    started = {v["quest"] for k, v in (p.get("item_sets") or {}).items() if k in ("A", "B")}
    started |= set((p.get("item_sets") or {}).get("after_quests", ()))
    # job 50 выгоднее, только пока Sir Andrew не выбрал набор (после rand проверки уровня нет, knight.txt:607-645)
    prefer_skip = bool(diff and all(d["hard"] for d in diff.values()) and skip_lv and not q & started)
    if prefer_skip:
        stages.append({"id": "job_skip", "title": f"уровень профессии {skip_lv}: Sir Andrew не просит предметов",
                       "done": job_lv >= skip_lv, "ref": p["skip"]["ref"]})
    sp = skill_points(state)
    stages.append({"id": "skill_points", "title": "потратить все очки навыков",
                   "done": None if sp is None else sp == 0})
    cur = current_stage(state, data, path, done)
    status = "done" if cur else "todo"
    for st in p["stages"]:
        if st is cur:
            status = "next"
        stages.append({"id": st["id"], "title": st["title"], "done": status == "done", "next": st is cur})
        if st is cur:
            status = "todo"

    unreachable = bool(ready["unreachable"])
    if job_lv < need_job:
        nxt = _goal_step("job_lv", f"добрать уровень профессии до {need_job} (сейчас {job_lv}) для {p['to']}",
                         data, now, target=need_job, have=job_lv)
    elif prefer_skip and job_lv < skip_lv:
        nxt = _goal_step("job_lv", f"добрать уровень профессии до {skip_lv} (сейчас {job_lv}): тогда Sir Andrew "
                         "не попросит трудных предметов", data, now, target=skip_lv, have=job_lv, ref=p["skip"]["ref"])
    elif sp:
        nxt = _goal_step("quest_stage", f"потратить очки навыков ({sp}): со свободными очками профессию не сменят",
                         data, now, ref=p["requirements"]["refs"][1])
    elif any(m["kind"] == "item" for m in ready["missing"]):
        need = [m for m in ready["missing"] if m["kind"] == "item"]
        text = "собрать для Sir Andrew: " + ", ".join(f"{m['name'] or m['id']} {m['have']}/{m['need']}" for m in need)
        nxt = _goal_step("collect_items", text, data, now, items=need)
    elif cur:
        nxt = _goal_step("quest_stage", f"квест {p['to']}: {cur['title']}", data, now, stage=cur["id"],
                         npc=(p["npcs"].get(cur.get("npc")) if cur.get("npc") else None))
    else:
        nxt = _goal_step("quest_stage", f"квест {p['to']}: этап по журналу не определён", data, now)
    if unreachable:
        nxt["unreachable"] = [m["id"] for m in ready["unreachable"]]
    return {"job": job, "path": path, "to": p["to"], "stages": stages, "next": nxt, "readiness": ready,
            "item_sets": diff, "prefer_job_skip": prefer_skip, "unreachable": unreachable}


def expired(goal, now=None):
    now = now if now is not None else time.time()
    return goal is None or goal.get("expires", 0) <= now


def stage_action(state, data, path=None, done=()):
    """Действие для тела (плагин jobChange): шаги текущего этапа. None — нечего делать или не готов."""
    path = path or path_for(state, data)
    stage = current_stage(state, data, path, done)
    if not stage:
        return None
    for need, value in (stage.get("needs") or {}).items():
        if need == "job_lv" and int(state.get("job_lv") or 0) < value:
            return None
        if need == "skill_points" and skill_points(state) != value:
            return None
        if need == "items_from_set":
            r = readiness(state, data, path)
            if any(m["kind"] == "item" for m in r["missing"]):
                return None
    return {"action": "job_change", "path": path, "stage": stage["id"], "steps": stage["steps"],
            "success": stage.get("success")}


# ---------- ответы в диалоге (то же правило в плагине jobChange) ----------

def choose_answer(answers, options, pos=None):
    """Номер пункта (с 1) для меню options. answers — ответы шага talk.
    ordered (pos — индекс следующего ответа): только answers[pos]; иначе первый ответ, текст которого
    точно совпадает с пунктом меню. None — меню не из сценария: отвечать нельзя."""
    opts = [o.strip() for o in options]
    cand = [answers[pos]] if pos is not None else answers
    for a in cand:
        if pos is not None and a is None:
            return None
        text = a["text"].strip()
        if text in opts:
            return opts.index(text) + 1
    return None


# ---------- снаряжение ----------

def _sex_ok(item, state):
    g = item.get("gender", "Both")
    sex = (state.get("sex") or "").lower()
    return g == "Both" or not sex or g.lower() == sex


def _stat(item, slot):
    return item["attack"] if slot == "weapon" else item["defense"]


def next_equipment(state, budget, data=None):
    """Что купить следующим: вещь из магазинов Пронтеры/Изюда, которую профессия носит на уровне lv,
    дешевле budget и лучше надетой. Слоты по порядку equipment.slots. None — покупать нечего."""
    data = data or load()
    cls = data["classes"].get(state.get("job") or "")
    if not cls:
        return None
    job, lv = cls["rathena"], int(state.get("lv") or 0)
    equip = state.get("equip")
    by_id = {e["id"]: e for e in data["catalog"]["equipment"]}
    two_hand = False
    if equip and equip.get("weapon") in by_id:
        two_hand = "Both_Hand" in by_id[equip["weapon"]]["locations"]
    for slot in data["equipment"]["slots"]:
        if slot == "Left_Hand" and (not cls.get("use_shield") or two_hand):
            continue
        cur_id = (equip or {}).get(slot)
        if cur_id and cur_id not in by_id:
            continue                                     # надето что-то не из магазинов — сравнить не с чем
        cur = _stat(by_id[cur_id], slot) if cur_id else 0
        best = None
        for e in data["catalog"]["equipment"]:
            if job not in e["jobs"] or e["equip_lv"] > lv or e["price"] > budget or not _sex_ok(e, state):
                continue
            if not set(e["locations"]) & set(SLOT_LOCATIONS[slot]):
                continue
            if slot == "weapon" and e["subtype"] not in cls["weapon_types"]:
                continue
            if slot != "weapon" and e["type"] != "Armor":
                continue
            if _stat(e, slot) <= cur:
                continue
            if best is None or (_stat(e, slot), -e["price"]) > (_stat(best, slot), -best["price"]):
                best = e
        if best:
            stat = "ATK" if slot == "weapon" else "DEF"
            return {"id": best["id"], "name": best["name"], "slot": slot, "price": best["price"],
                    "stat": _stat(best, slot), "gain": _stat(best, slot) - cur, "shop": best["shops"][0],
                    "equip_unknown": equip is None,
                    "reason": f"{best['name']}: {stat} {_stat(best, slot)} (+{_stat(best, slot) - cur}), "
                              f"{best['price']}z, {best['shops'][0]['map']} {best['shops'][0]['npc']}"}
    return None


# ---------- для промпта ----------

def summary(state, data=None, now=None, budget=None):
    """Короткий текст для промпта модели: путь, следующий шаг, недостающее, покупка."""
    data = data or load()
    g = plan(state, data, now)
    parts = []
    if g["path"]:
        parts.append(f"путь {state.get('job')} -> {g['to']}")
    parts.append("следующее: " + g["next"]["text"])
    r = g.get("readiness") or {}
    miss = [m for m in r.get("missing", []) if m["kind"] != "item"]
    if miss:
        parts.append("не хватает: " + ", ".join(f"{m['kind']} {m.get('have')}/{m['need']}" for m in miss))
    if r.get("unknown"):
        parts.append("неизвестно: " + ", ".join(u["kind"] for u in r["unknown"]))
    if g.get("unreachable"):
        parts.append("недостижимо: предметы " + ", ".join(str(i) for i in g["next"].get("unreachable", [])))
    if budget is None:
        budget = int(int(state.get("zeny") or 0) * data["equipment"]["max_share_of_zeny"])
    eq = next_equipment(state, budget, data)
    if eq:
        parts.append("купить: " + eq["reason"])
    return "; ".join(parts)
