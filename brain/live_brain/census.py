"""Перепись мира и «кого родить следующим» (ORG-088, ТЗ Т-26). Только чтение и только совет.

Состав мира — по реестру brain/world/roster.json и памяти жителей state/<bot>/memory.sqlite (mode=ro): профессия,
уровни, карта, роль (лекарь, торговец, танк, дальний бой, маг, ловкач, новичок), группа, связи с жителями, занятия
за 7 дней. Затем — каких ролей не хватает и что из-за этого спит в коде, рекомендация шаблона bots/templates с
объяснением, ресурсы (как scripts/lab resources, ORG-047; LAB_MAX_ONLINE, ORG-044) и блокеры рождения (start_point,
auto_job_change, темп ORG-043 — docs/POPULATION.md §4, §6). Ничего не пишет: ни БД, ни реестр, ни профили.
Рождает владелец (scripts/lab new-resident, birth).

    python3 -m live_brain.census --lab-root /opt/ro-bot-lab --bots bot01 bot02 [--max-online N] [--json]
    scripts/lab census [--json]
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import resources
from . import flaws as flaws_mod                                        # traits: ORG-101
from . import drift as drift_mod                                        # traits: ORG-092
from . import interests as interests_mod                                # interests: ORG-103
from .atlas import ARCHETYPES
from .economy import MERCHANTS
from .healer import DEFAULTS as HEALER_DEFAULTS, HEALER_JOBS

REPO = Path(__file__).resolve().parents[2]
ACTIVITY_DAYS = 7
HISTORY_FRESH = 3600            # замер истории сторожа моложе часа ещё годится для оценки

TANK_JOBS = ("Swordman", "Swordsman", "Knight", "Crusader", "Lord Knight", "Paladin", "Rune Knight", "Royal Guard")
THIEF_JOBS = ("Thief", "Assassin", "Rogue", "Assassin Cross", "Stalker", "Guillotine Cross", "Shadow Chaser")
ROLE_RU = {"healer": "лекарь", "merchant": "торговец", "tank": "танк", "ranged": "дальний бой", "magic": "маг",
           "thief": "ловкач", "novice": "новичок", "other": "другое"}
# Важность роли для мира и что без неё спит. Только правда по коду (healer.py, economy.py, boss.py).
NEEDS = {
    "merchant": (3, "нет торговца: лавка offer_shop (ORG-034) спит, лут жителей некому перекупать с Overcharge "
                    "(economy.pick_buyer зовёт Merchant первым)"),
    "healer": (3, "нет лекаря: пост у собора (ORG-069) пуст, мини-боссу (ORG-079, выкл.) нужен хилер "
                  "или ≥ 10 зелий у каждого"),
    "tank": (2, "нет танка (Swordman-ветка): некому первым держать монстров в группе (кода, который этого ждёт, нет)"),
    "ranged": (1, "нет дальнего боя (Archer-ветка): однообразие боя (ремесло стрел ORG-075 — только идея)"),
    "magic": (1, "нет мага: однообразие боя (кода, который ждёт мага, нет)"),
    "thief": (1, "нет ловкача (Thief-ветка): однообразие боя (кода, который ждёт ловкача, нет)"),
}
# Известные преграды первой профессии нового жителя (docs/POPULATION.md §6.3), штраф к баллу.
JOB_BLOCKERS = {
    "Thief": (0.5, "первая профессия: телепорт Kafra в Морокко стоит 1200 z — новичку их надо накопить"),
    "Swordsman": (0.25, "первая профессия: путь через перестроенный izlude (таблицы профиля), в игре не проверен"),
}


def role_of(job):
    """Роль по профессии (имя OpenKore или реестра)."""
    j = (job or "").strip()
    if not j:
        return "other"
    if j == "Novice" or j.lower().startswith("novice") or j == "High Novice":
        return "novice"
    if j in HEALER_JOBS:
        return "healer"
    if j in MERCHANTS:
        return "merchant"
    if j in TANK_JOBS:
        return "tank"
    if j in THIEF_JOBS:
        return "thief"
    low = j.lower()
    if low in ARCHETYPES["ranged"]:
        return "ranged"
    if low in ARCHETYPES["magic"]:
        return "magic"
    return "other"


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _loads(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def read_memory(db_path, now, names):
    """kv last_state, занятия за 7 дней, отношения к жителям. Только mode=ro; нет файла — None."""
    if not Path(db_path).is_file():
        return None
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    try:
        row = db.execute("SELECT value FROM kv WHERE key = 'last_state'").fetchone()
        st = _loads(row[0]) if row else None
        acts = {}
        for (data,) in db.execute("SELECT data FROM events WHERE kind = 'activity' AND ts >= ?",
                                  (now - ACTIVITY_DAYS * 86400,)):
            name = (_loads(data) or {}).get("name")
            if name:
                acts[name] = acts.get(name, 0) + 1
        try:
            rels = {n: int(a or 0) for n, a in db.execute("SELECT name, affinity FROM relations") if n in names}
        except sqlite3.OperationalError:
            rels = {}
        row = db.execute("SELECT value FROM kv WHERE key = 'traits_drift'").fetchone()   # traits: ORG-092
        drift = _loads(row[0]) if row else None                                         # traits:
        row = db.execute("SELECT value FROM kv WHERE key = 'legacy'").fetchone()   # legacy: ORG-083
        legacy = _loads(row[0]) if row else None                                  # legacy:
    except sqlite3.DatabaseError:
        return None
    finally:
        db.close()
    return {"state": st if isinstance(st, dict) else {}, "activities": acts, "relations": rels,
            "legacy": legacy if isinstance(legacy, dict) else None,             # legacy:
            "drift": drift if isinstance(drift, dict) else None}                # traits: ORG-092


def persona_interests(repo, persona):                                   # interests: ORG-103
    """Интересы жителя по его персоне brain/personas/<persona>.json (поле interests или вывод из черт)."""
    p = _json(Path(repo) / "brain" / "personas" / f"{persona}.json")
    return interests_mod.interests(p) if isinstance(p, dict) and p else []


def persona_flaw(repo, persona):                                        # traits: ORG-101
    p = _json(Path(repo) / "brain" / "personas" / f"{persona}.json")
    return flaws_mod.label(p) if isinstance(p, dict) else None


def residents(repo, lab_root, lab_bots, now):
    doc = _json(Path(repo) / "brain" / "world" / "roster.json", {}) or {}
    reg = doc.get("residents") or {}
    names = {o.get("name") for o in reg.values() if isinstance(o, dict)}
    out = []
    for bot in sorted(set(reg) | set(lab_bots), key=lambda b: (int(re.sub(r"\D", "", b) or 0), b)):
        o = reg.get(bot) or {}
        mem = read_memory(Path(lab_root) / "state" / bot / "memory.sqlite", now, names) if lab_root else None
        st = (mem or {}).get("state") or {}
        job = st.get("job") or o.get("job")
        out.append({
            "bot": bot, "name": st.get("name") or o.get("name") or bot, "target": o.get("job"), "job": job,
            "lv": st.get("lv"), "job_lv": st.get("job_lv"), "map": st.get("map"), "role": role_of(job),
            "target_role": role_of(o.get("job")), "active": bool(o.get("active")), "in_registry": bot in reg,
            "in_lab": bot in lab_bots, "born": o.get("born"), "template": o.get("template"),
            "persona": o.get("persona"), "party": st.get("party") or None, "memory": mem is not None,
            "relations": (mem or {}).get("relations") or {},
            "activities": sorted(((mem or {}).get("activities") or {}).items(), key=lambda kv: -kv[1])[:3],
            "legacy": (mem or {}).get("legacy"),                                  # legacy: ORG-083
            "interests": persona_interests(repo, o.get("persona") or bot),        # interests: ORG-103
            "flaw": persona_flaw(repo, o.get("persona") or bot),                  # traits: ORG-101
            "drift": drift_mod.report_line((mem or {}).get("drift")) if (mem or {}).get("drift") else None,  # traits:
        })
    return out


def world(rs):
    """Жители мира: активные в реестре (их видят другие; docs/POPULATION.md §1)."""
    return [r for r in rs if r["active"]]


def _role(r):
    """Роль сейчас; новичок считается по цели (профессии из реестра) — он к ней идёт."""
    return r["target_role"] if r["role"] == "novice" else r["role"]


def gaps(rs):
    live = world(rs)
    have = {_role(r) for r in live}
    out = [{"role": role, "weight": w, "text": text} for role, (w, text) in NEEDS.items() if role not in have]
    parties = {}
    for r in live:
        if r["party"]:
            parties.setdefault(r["party"], []).append(r)
    for party, members in sorted(parties.items()):
        if len(members) >= 2 and not any(_role(m) == "healer" for m in members):
            out.append({"role": "healer", "weight": 1, "party": party,
                        "text": f"в группе «{party}» ({', '.join(m['name'] for m in members)}) нет лекаря"})
    return out


def templates(repo):
    out = []
    root = Path(repo) / "bots" / "templates"
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        t = _json(d / "template.json")
        if isinstance(t, dict) and t.get("job"):
            out.append({"template": d.name, "job": t["job"], "name": t.get("default_name"),
                        "persona": bool(t.get("persona")) and (d / str(t["persona"])).is_file()})
    return out


def recommend(repo, rs, gs):
    live = world(rs)
    by_role = {}
    for g in gs:
        by_role[g["role"]] = by_role.get(g["role"], 0) + g["weight"]
    same = {}
    for r in live:
        same[r["target"] or r["job"]] = same.get(r["target"] or r["job"], 0) + 1
    out = []
    for t in templates(repo):
        role = role_of(t["job"])
        score = by_role.get(role, 0) - same.get(t["job"], 0)
        why = [g["text"] for g in gs if g["role"] == role]
        if same.get(t["job"]):
            why.append(f"{t['job']} в мире уже {same[t['job']]}")
        blockers = []
        if t["job"] in JOB_BLOCKERS:
            pen, text = JOB_BLOCKERS[t["job"]]
            score -= pen
            blockers.append(text)
        if not t["persona"]:
            blockers.append("у шаблона нет своей персоны: new-resident … --persona FILE")
        out.append({"template": t["template"], "job": t["job"], "name": t["name"], "role": role,
                    "score": round(score, 2), "why": why, "blockers": blockers})
    out.sort(key=lambda x: -x["score"])            # sort стабилен: при равенстве — порядок шаблонов
    return out


def capacity(lab_root, lab_bots, max_online=None, proc="/proc", now=None, reserve_mb=256):
    """Сколько ещё жителей влезет: живой замер /proc, иначе свежий замер истории сторожа; LAB_MAX_ONLINE."""
    now = now or time.time()
    out = {"more": None, "online": None, "source": None, "max_online": max_online, "notes": []}
    procs = resources.scan(lab_root, lab_bots, proc) if lab_root else []
    mem = resources.meminfo(proc)
    cap = resources.capacity(procs, mem, reserve_mb)
    if cap:
        out.update(more=cap["more"], online=cap["online"], source="/proc сейчас")
    elif lab_root:
        rows = resources.read_history(Path(lab_root) / "logs" / "resources.jsonl", now - HISTORY_FRESH)
        rows = [r for r in rows if r.get("mem_available_kb")]
        if rows:
            last = rows[-1]
            cap = resources.capacity(last.get("procs") or [], {"MemAvailable": last["mem_available_kb"]}, reserve_mb)
            if cap:
                out.update(more=cap["more"], online=cap["online"], source="история сторожа (последний замер)")
    if out["more"] is None:
        out["notes"].append("оценки ресурсов нет: ни одно тело не запущено и свежей истории замеров нет "
                            "(scripts/lab resources)")
    elif out["more"] < 1:
        out["notes"].append("ресурсов на ещё одного жителя (тело + мозг) нет: сначала освободите память "
                            "или ограничьте онлайн (LAB_MAX_ONLINE)")
    n = len(lab_bots)
    if max_online is not None:
        if n + 1 > max_online:
            out["notes"].append(f"LAB_MAX_ONLINE={max_online}, жителей в LAB_BOTS {n}: новый житель будет ждать "
                                f"смены (сторож держит онлайн не больше {max_online}, docs/POPULATION.md §5)")
    return out


def birth_blockers(repo, rs, now):
    out = []
    prog = _json(Path(repo) / "brain" / "world" / "progression.json", {}) or {}
    start = prog.get("start") or {}
    if not (start.get("override") or {}).get("enabled"):
        exit_ok = (start.get("academy_exit") or {}).get("status") == "ok"
        out.append("start_point в Пронтере не включён (server/conf/optional/char_start_point.txt, решение "
                   "владельца): новичок появится в учебном полигоне iz_int; "
                   + ("выход в izlude есть по данным (NB-1), в игре не проверен" if exit_ok
                      else "выход не автоматизирован — житель застрянет")
                   + " (docs/POPULATION.md §6.1–6.2)")
    goals = _json(Path(repo) / "brain" / "world" / "goals.json", {}) or {}
    if not (goals.get("progression") or {}).get("auto_job_change"):
        out.append("progression.auto_job_change выключен: первую профессию новичок сам не сменит "
                   "(цель видна в отчёте, сменить может оператор; docs/POPULATION.md §6.3)")
    today = datetime.fromtimestamp(now, timezone.utc).date()
    recent = []
    for r in rs:
        try:
            born = datetime.strptime(r["born"] or "", "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - born).days < 7:
            recent.append(f"{r['name']} ({r['born']})")
    if recent:
        out.append("темп ORG-043 — не чаще одного жителя в неделю; недавно родились: " + ", ".join(recent))
    waiting = [r for r in rs if r["in_registry"] and not r["active"]]
    if waiting:
        out.append("в реестре уже ждут рождения (active: false): "
                   + ", ".join(f"{r['bot']} {r['name']} ({r['target']})" for r in waiting)
                   + " — сначала доведите их (scripts/lab birth botNN)")
    return out


def next_bot(rs):
    nums = [int(re.sub(r"\D", "", r["bot"]) or 0) for r in rs]
    return f"bot{(max(nums) + 1 if nums else 1):02d}"


def collect(repo, lab_root, lab_bots, max_online=None, now=None, proc="/proc"):
    now = now or time.time()
    rs = residents(repo, lab_root, list(lab_bots), now)
    gs = gaps(rs)
    return {"now": now, "residents": rs, "gaps": gs, "recommend": recommend(repo, rs, gs),
            "capacity": capacity(lab_root, list(lab_bots), max_online, proc, now),
            "blockers": birth_blockers(repo, rs, now), "next_bot": next_bot(rs),
            "retire": retire_advice(rs)}                                         # legacy: ORG-083


def retire_advice(rs):                                                   # legacy: ORG-083
    """Жители, ушедшие на покой (kv legacy.phase retired) или решившие уйти: совет владельцу, ничего не меняет."""
    out = []
    for r in rs:
        lg = r.get("legacy") or {}
        if lg.get("phase") not in ("ready", "bequest", "farewell", "retired"):
            continue
        done = lg.get("phase") == "retired"
        item = {"bot": r["bot"], "name": r["name"], "phase": lg.get("phase"), "reasons": lg.get("reasons") or [],
                "heir": lg.get("heir"), "gifts": lg.get("gifts"), "active": r["active"]}
        if done and r["active"]:
            item["todo"] = (f"в brain/world/roster.json у {r['bot']} поставить \"active\": false, затем "
                            f"scripts/lab roster sync --write, убрать {r['bot']} из LAB_BOTS и остановить штатно: "
                            f"scripts/lab stop live {r['bot']}")
        out.append(item)
    return out


def _lv(r):
    if r["lv"] is None:
        return "ур. ?"
    return f"ур. {r['lv']}/{r['job_lv'] if r['job_lv'] is not None else '?'}"


def render(data):
    rs = data["residents"]
    lines = ["== перепись мира (ORG-088, только чтение)"]
    live = world(rs)
    counts = {}
    for r in live:
        counts[_role(r)] = counts.get(_role(r), 0) + 1
    lines.append(f"   жителей мира: {len(live)}"
                 + (" — " + ", ".join(f"{ROLE_RU[k]} {v}" for k, v in sorted(counts.items())) if counts else ""))
    for r in rs:
        flags = []
        if not r["active"]:
            flags.append("не активен в реестре" if r["in_registry"] else "нет в реестре")
        if not r["in_lab"]:
            flags.append("не в LAB_BOTS")
        if not r["memory"]:
            flags.append("памяти нет")
        job = r["job"] or "?"
        if r["target"] and r["target"] != job and r["role"] == "novice":
            job += f" → {r['target']}"
        head = f"   {r['bot']} {r['name']}: {job}, {_lv(r)}, роль {ROLE_RU[_role(r)]}"
        if r["map"]:
            head += f", {r['map']}"
        if r["party"]:
            head += f", группа «{r['party']}»"
        lines.append(head + (f" [{'; '.join(flags)}]" if flags else ""))
        if r.get("interests"):                                              # interests: ORG-103
            lines.append("      интересы: " + ", ".join(interests_mod.RU.get(x, x) for x in r["interests"]))
        if r.get("drift"):                                                  # traits: ORG-092
            lines.append("      " + r["drift"])                                 # traits:
        if r.get("flaw"):                                                   # traits: ORG-101
            lines.append("      " + r["flaw"])                                  # traits:
        if r["relations"]:
            lines.append("      связи: " + ", ".join(f"{n} {a:+d}" for n, a in sorted(r["relations"].items())))
        if r["activities"]:
            lines.append(f"      занятия за {ACTIVITY_DAYS} дн.: " + ", ".join(f"{n} ×{c}" for n, c in r["activities"]))
    lines.append("== чего не хватает")
    lines += [f"   - {g['text']}" for g in data["gaps"]] or ["   все роли есть"]
    lines.append("== кого родить следующим (совет; рождает владелец — docs/POPULATION.md §4)")
    best = data["recommend"][0] if data["recommend"] else None
    if best:
        name = f" ({best['name']})" if best["name"] else ""
        lines.append(f"   следующий — {best['job']}{name}: scripts/lab new-resident {data['next_bot']} "
                     f"{best['template']} <Имя>")
        lines.append("   почему: " + ("; ".join(best["why"]) if best["why"] else "дефицита нет — по разнообразию"))
        for b in best["blockers"]:
            lines.append(f"   учесть: {b}")
        rest = [f"{t['job']} {t['score']:+g}" for t in data["recommend"][1:]]
        if rest:
            lines.append("   остальные шаблоны (балл): " + ", ".join(rest))
    else:
        lines.append("   шаблонов нет (bots/templates)")
    cap = data["capacity"]
    lines.append("== ресурсы (ORG-047, ORG-044)")
    if cap["more"] is not None:
        lines.append(f"   влезет ещё ≈ {cap['more']} (онлайн тел {cap['online']}; {cap['source']})")
    lines.append(f"   LAB_MAX_ONLINE: {cap['max_online'] if cap['max_online'] is not None else 'не задан (все из LAB_BOTS)'}")
    lines += [f"   ! {n}" for n in cap["notes"]]
    lines.append("== блокеры рождения")
    lines += [f"   ! {b}" for b in data["blockers"]] or ["   нет"]
    if data.get("retire"):                                                    # legacy: ORG-083
        lines.append("== уход на покой (ORG-083, совет владельцу; мозг процессы и реестр не трогает)")
        for t in data["retire"]:
            what = "ушёл(ушла) на покой" if t["phase"] == "retired" else f"готовится уйти ({t['phase']})"
            heir = f"; наследник {t['heir']}, посылок подтверждено: {t['gifts'] or 0}" if t["heir"] else ""
            lines.append(f"   {t['bot']} {t['name']}: {what} — {'; '.join(t['reasons']) or '?'}{heir}")
            if t.get("todo"):
                lines.append(f"      сделать: {t['todo']}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", default=str(REPO))
    p.add_argument("--lab-root")
    p.add_argument("--bots", nargs="*", default=[])
    p.add_argument("--max-online", default="")
    p.add_argument("--proc", default="/proc")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    mo = int(a.max_online) if str(a.max_online).isdigit() else None
    data = collect(a.repo, a.lab_root, a.bots, mo, proc=a.proc)
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(render(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
