"""timefix: фиксированное время мира для тестов, которые не о времени суток.

Распорядок (сон 23:30–08:00 по UTC+3), ночь social, вечерний круг 20–21 ч, день недели и праздники
календаря зависят от «сейчас». Тест, запущенный в случайный час, не должен от этого зависеть, поэтому
тесты с настоящим Mind / процессом мозга живут в полдень мира фиксированного обычного дня (REF_DAY):
часы идут (time.time() тикает), но с постоянным сдвигом.

  shift_time(self)        — в процессе теста: time.time() -> полдень REF_DAY + прошедшее время;
  brain_argv(shift, ...)  — команда процесса мозга с тем же сдвигом (вместо `python -m live_brain`).
Тесту и мозгу нужен один и тот же сдвиг: ts сообщений, inbox и записи памяти сравниваются между ними.
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

WORLD_TZ = timezone(timedelta(hours=3))          # goals.json timezone_offset_hours
REF_DAY = (2026, 10, 8)                          # четверг: без праздника и без множителей дня недели


def world_ts(hour=12, minute=0, day=REF_DAY):
    """Unix-время часа мира в день day (по умолчанию — полдень REF_DAY)."""
    return datetime(*day, hour, minute, tzinfo=WORLD_TZ).timestamp()


def shift_time(test, target=None):
    """Подменить time.time() до конца теста: «сейчас» = target (полдень REF_DAY) + прошедшее время.
    Модули мозга читают часы при вызове (clock = lambda: time.time()), поэтому подмена видна всем.
    Возвращает сдвиг в секундах — его же передать процессу мозга (brain_argv)."""
    real = time.time
    shift = (world_ts() if target is None else target) - real()
    patcher = mock.patch("time.time", new=lambda: real() + shift)
    patcher.start()
    test.addCleanup(patcher.stop)
    return shift


BOOT = ("import runpy, sys, time; _r = time.time; _s = float(sys.argv.pop(1)); "
        "time.time = lambda: _r() + _s; runpy.run_module('live_brain', run_name='__main__', alter_sys=True)")


def brain_argv(shift, *args):
    """Аргументы запуска `python3 -m live_brain *args` со сдвигом часов shift (из shift_time)."""
    return [sys.executable, "-c", BOOT, repr(shift), *args]
