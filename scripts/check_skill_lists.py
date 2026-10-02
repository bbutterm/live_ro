#!/usr/bin/env python3
"""Порядок прокачки навыков в bots/combat/classes.json против дерева навыков rAthena.

Плагин OpenKore raiseSkill берёт ПЕРВЫЙ недокачанный навык списка; если у него не выполнены
требования (или навыка нет в дереве), raiseSkill отключается совсем — прокачка встаёт навсегда.
Поэтому в списке требования должны идти раньше. Проверка моделирует список с наследованием
(как combatProfile: сначала навыки родителя, потом свои) и печатает ошибки.

    scripts/check_skill_lists.py [upstream/rathena]          # код выхода 1 — есть ошибки
    scripts/check_skill_lists.py --fix [upstream/rathena]    # вставить требования перед навыками
"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
# Имя профессии в classes.json -> Job в db/re/skill_tree.yml (там «Swordman», «Taekwon» и т.д.)
JOB_NAMES = {"Swordsman": "Swordman", "Novice": "Novice", "Super Novice": "Supernovice"}


def tree(rathena):
    data = yaml.safe_load((Path(rathena) / "db" / "re" / "skill_tree.yml").read_text(encoding="utf-8"))
    out = {}
    for body in data["Body"]:
        out[body["Job"]] = {t["Name"]: (t.get("MaxLevel", 1), {r["Name"]: r["Level"] for r in t.get("Requires") or []})
                            for t in body.get("Tree") or []}
    return out


def chain(classes, name):
    out, n = [], name
    while n and len(out) < 5:
        out.append(n)
        n = classes[n].get("inherits")
    return out[::-1]                          # от первого родителя к самой профессии


def check(rathena):
    classes = json.loads((ROOT / "bots" / "combat" / "classes.json").read_text(encoding="utf-8"))["classes"]
    sk = tree(rathena)
    errors = []
    for name in sorted(classes):
        known, level = {}, {}
        for n in chain(classes, name):
            known.update(sk.get(JOB_NAMES.get(n, n), {}))
        for n in chain(classes, name):
            for step in filter(None, (s.strip() for s in classes[n].get("skills", "").split(","))):
                skill, want = step.split()[0], int(step.split()[1])
                if skill not in known:
                    errors.append(f"{name}: {skill} нет в дереве {'/'.join(chain(classes, name))}")
                    continue
                max_lv, req = known[skill]
                if want > max_lv:
                    errors.append(f"{name}: {skill} {want} > максимума {max_lv}")
                if want > level.get(skill, 0):
                    missing = [f"{r} {lv}" for r, lv in req.items() if level.get(r, 0) < lv]
                    if missing:
                        errors.append(f"{name}: {skill} {want} раньше требований: {', '.join(missing)}")
                    level[skill] = min(want, max_lv)
    return errors


def fixed_list(steps, known, level):
    """Свой список профессии с требованиями, вставленными перед навыком (рекурсивно), без повторов."""
    out = []

    def need(skill, lv, depth=0):
        if level.get(skill, 0) >= lv or skill not in known or depth > 6:
            return
        for r, rlv in known[skill][1].items():
            need(r, rlv, depth + 1)
        lv = min(lv, known[skill][0])
        out.append(f"{skill} {lv}")
        level[skill] = lv

    for step in steps:
        skill, want = step.split()[0], int(step.split()[1])
        if skill in known:
            need(skill, want)
    return ", ".join(out)


def fix(rathena):
    path = ROOT / "bots" / "combat" / "classes.json"
    text = path.read_text(encoding="utf-8")
    classes = json.loads(text)["classes"]
    sk = tree(rathena)
    for name in sorted(classes):
        known, level = {}, {}
        for n in chain(classes, name):
            known.update(sk.get(JOB_NAMES.get(n, n), {}))
        for n in chain(classes, name)[:-1]:                 # уровни, которые дадут списки родителей
            for step in filter(None, (x.strip() for x in classes[n].get("skills", "").split(","))):
                level[step.split()[0]] = max(level.get(step.split()[0], 0), int(step.split()[1]))
        old = classes[name].get("skills", "")
        steps = [x.strip() for x in old.split(",") if x.strip()]
        new = fixed_list(steps, known, level)
        if new and new != old:
            needle = f'"skills": "{old}"'
            assert text.count(needle) == 1, name
            text = text.replace(needle, f'"skills": "{new}"')
            print(f"{name}: {old}  ->  {new}")
    path.write_text(text, encoding="utf-8")


def main(argv):
    if argv and argv[0] == "--fix":
        fix(argv[1] if len(argv) > 1 else ROOT / "upstream" / "rathena")
        argv = argv[2:]
    errors = check(argv[0] if argv else ROOT / "upstream" / "rathena")
    for e in errors:
        print(e)
    print("порядок навыков OK" if not errors else f"ошибок: {len(errors)}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
