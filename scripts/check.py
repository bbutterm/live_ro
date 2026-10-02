#!/usr/bin/env python3
"""Статическая проверка репозитория live_ro (без сборки и без runtime).

Проверяет:
  - submodules инициализированы, HEAD = закреплённому gitlink и = PINS ниже;
  - внутри submodules нет незакоммиченных изменений;
  - PACKETVER из server/build.conf совпадает с serverType профилей ботов;
  - в отслеживаемых файлах нет очевидных секретов, баз, логов и бинарников.

Usage: python3 scripts/check.py [repo_root]
"""
from pathlib import Path
import re
import subprocess
import sys

# Второй, намеренно ручной уровень закрепления: обновление upstream требует
# правки и gitlink, и этой таблицы (см. docs/SUBMODULES.md).
PINS = {
    "upstream/rathena": "e985006171d2eb320ee512a653f4c83aea3d81b6",
    "upstream/openkore": "51de1ddfc4449ae5217f6886de702f87ca934030",
}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-or-v1-[0-9a-f]{20,}"),  # OpenRouter
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),  # Telegram bot token
]
FORBIDDEN_FILES = re.compile(
    r"(^|/)(lab\.env|live_ro\.env|\.env|my\.cnf)$|\.(sqlite3?|db|log|sql\.gz|dump|so|dll|exe|o)$"
    r"|(^|/)(secrets|state|logs|releases|backups|run)/"
)
ALLOWED_FILES = {"live_ro.env.example"}
# Ключи с паролями в шаблонах rAthena: значение обязано быть плейсхолдером.
SECRET_KEYS = re.compile(r"^\s*(\w+_pw|passwd|userid|\w+_id)\s*:\s*(.*)$")

root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
errors = []


def git(*args, cwd=root):
    return subprocess.run(["git", "-C", str(cwd), *args], text=True,
                          capture_output=True)


def check_submodules():
    for path, pin in PINS.items():
        line = git("ls-tree", "HEAD", path).stdout.split()
        if len(line) < 3 or line[1] != "commit":
            errors.append(f"{path}: в HEAD нет gitlink submodule")
            continue
        gitlink = line[2]
        if gitlink != pin:
            errors.append(f"{path}: gitlink {gitlink[:12]} != PINS {pin[:12]}"
                          " (обновление upstream требует проверки совместимости)")
        head = git("rev-parse", "HEAD", cwd=root / path)
        if head.returncode != 0 or not (root / path / ".git").exists():
            errors.append(f"{path}: не инициализирован; выполните "
                          "git submodule update --init --recursive")
            continue
        if head.stdout.strip() != gitlink:
            errors.append(f"{path}: checkout {head.stdout.strip()[:12]} != "
                          f"закреплённый {gitlink[:12]}")
        dirty = git("status", "--porcelain", cwd=root / path).stdout.strip()
        if dirty:
            errors.append(f"{path}: незакоммиченные изменения внутри submodule "
                          "(оформите патчем в server/patches, см. docs/SUBMODULES.md)")
        else:
            print(f"{path}: pinned {gitlink[:12]} OK, clean")


def check_packetver():
    conf = root / "server" / "build.conf"
    m = re.search(r"^PACKETVER=(\d{4})(\d{2})(\d{2})$", conf.read_text(), re.M)
    if not m:
        errors.append("server/build.conf: нет PACKETVER=YYYYMMDD")
        return
    date = "_".join(m.groups())
    for servers in sorted(root.glob("bots/*/tables/servers.txt")):
        types = re.findall(r"^serverType\s+(\S+)", servers.read_text(), re.M)
        bad = [t for t in types if date not in t]
        if not types or bad:
            errors.append(f"{servers.relative_to(root)}: serverType {types} "
                          f"не соответствует PACKETVER {''.join(m.groups())}")
    print(f"PACKETVER {''.join(m.groups())}: consistent with bot profiles"
          if not any("PACKETVER" in e for e in errors) else "PACKETVER: mismatch")


def check_tracked_files():
    files = git("ls-files", "-z").stdout.split("\0")
    for name in filter(None, files):
        if name in PINS:
            continue
        if FORBIDDEN_FILES.search(name) and name not in ALLOWED_FILES:
            errors.append(f"{name}: такой файл нельзя коммитить (секрет/база/лог/бинарник)")
            continue
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            errors.append(f"{name}: бинарный файл в репозитории")
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                errors.append(f"{name}: похоже на секрет ({pat.pattern[:24]}...)")
        if re.fullmatch(r"bots/[^/]+/control/config\.txt", name):
            for key in ("username", "password", "storageAuto_password",
                        "adminPassword", "secureAdminPassword"):
                m = re.search(rf"^{key}[ \t]+(\S.*)$", text, re.M)
                if m:
                    errors.append(f"{name}: {key} должен быть пустым в Git")
        if name.startswith("server/conf/") and name.endswith(".txt"):
            for n, line in enumerate(text.splitlines(), 1):
                m = SECRET_KEYS.match(line)
                if m and not re.fullmatch(r"@@[A-Z0-9_]+@@", m.group(2).strip()):
                    errors.append(f"{name}:{n}: {m.group(1)} должен быть плейсхолдером @@...@@")
    print("tracked files: secret scan done")


check_submodules()
check_packetver()
check_tracked_files()
if errors:
    print("\nОШИБКИ:", *errors, sep="\n  - ", file=sys.stderr)
    sys.exit(1)
print("Repository checks OK; runtime not tested")
