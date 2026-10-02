#!/usr/bin/env python3
"""Рендер runtime-конфигов live_ro из шаблонов и локального env-файла (secrets/live_ro.env).

Использование (вызывается из scripts/lab):
  render.py server <templates_dir> <dest_dir> <env_file> <lab_root>
  render.py bot <src_control_dir> <dest_control_dir> <env_file> <bot_id>

Секреты читаются только из env-файла и пишутся только в dest (права 600).
"""
from pathlib import Path
import os
import re
import shutil
import sys

PLACEHOLDER = re.compile(r"@@([A-Z0-9_]+)@@")
UNSET = {"", "CHANGE_ME"}
# rAthena хранит userid/passwd в буфере 24 байта: максимум 23 ASCII-символа.
CRED_KEY = re.compile(r"^(INTER|BOT\d+)_(USER|PASS)$")
CRED_VALUE = re.compile(r"^[!-~]{1,23}$")


def die(msg):
    sys.exit(f"render: {msg}")


def load_env(path):
    env = {}
    for n, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            die(f"{path}:{n}: ожидается KEY=VALUE")
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def require(env, key, source):
    value = env.get(key)
    if value is None or value in UNSET:
        die(f"{source}: переменная {key} не заполнена в env-файле")
    if "\n" in value or "\r" in value:
        die(f"{key}: перевод строки в значении недопустим")
    if CRED_KEY.match(key) and not CRED_VALUE.match(value):
        die(f"{key}: нужно 1-23 печатных ASCII-символа без пробелов (длина {len(value)})")
    return value


def write_private(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def render_server(templates, dest, env_file, lab_root):
    env = load_env(env_file)
    env["LAB_ROOT"] = lab_root
    files = sorted(Path(templates).glob("*.txt"))
    if not files:
        die(f"нет шаблонов в {templates}")
    for tpl in files:
        text = tpl.read_text()
        out = PLACEHOLDER.sub(lambda m: require(env, m.group(1), tpl.name), text)
        write_private(Path(dest) / tpl.name, out)
        print(f"rendered {tpl.name}")


def render_bot(src, dest, env_file, bot_id):
    env = load_env(env_file)
    prefix = bot_id.upper()
    user = require(env, f"{prefix}_USER", "config.txt")
    password = require(env, f"{prefix}_PASS", "config.txt")
    slot = env.get(f"{prefix}_CHAR_SLOT", "")
    if slot and not slot.isdigit():
        die(f"{prefix}_CHAR_SLOT должен быть числом")
    values = {"username": user, "password": password}
    if slot:
        values["char"] = slot
    src, dest = Path(src), Path(dest)
    if not (src / "config.txt").is_file():
        die(f"нет {src}/config.txt")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    os.chmod(dest, 0o700)
    cfg = dest / "config.txt"
    lines = cfg.read_text().splitlines(keepends=True)
    seen = set()
    for i, line in enumerate(lines):
        key = line.split(None, 1)[0] if line.strip() else ""
        if key in values and key not in seen:
            lines[i] = f"{key} {values[key]}\n"
            seen.add(key)
    if seen != set(values):
        die(f"в config.txt нет строк {sorted(set(values) - seen)}")
    write_private(cfg, "".join(lines))
    print(f"rendered {bot_id} control -> {dest}")


def main(argv):
    if len(argv) == 6 and argv[1] == "server":
        render_server(*argv[2:])
    elif len(argv) == 6 and argv[1] == "bot":
        render_bot(*argv[2:])
    else:
        die(__doc__)


if __name__ == "__main__":
    main(sys.argv)
