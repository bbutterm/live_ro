"""ORG-044: мягкая смена сторожа (scripts/lab shift_balance, may_start_bot) на поддельных процессах.

Функции scripts/lab подключаются без main; running_pid/stop_bot/shift_states подменены — ни один процесс
не запускается и не останавливается. Запуск: cd brain && python3 -m unittest -v tests.test_lab_shift
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parents[2] / "scripts" / "lab"

HARNESS = r'''
set -euo pipefail
src=$(sed '$d' "$LAB_SCRIPT")          # без последней строки main "$@"
eval "$src"
RUNNING=" $RUNNING_BOTS "
running_pid() { local n="$1"; [[ "$n" == brain-* ]] && { echo 1; return; }; [[ "$RUNNING" == *" $n "* ]] && echo 1 || true; }
stop_bot() { echo "STOP $1" >> "$LAB_ROOT/actions"; RUNNING=${RUNNING/ $1 / }; }
shift_states() { printf '%s\n' $STATES | tr ':' ' '; }
lab_bots() { printf '%s\n' $LAB_BOTS; }
"$@"
'''


@unittest.skipUnless(shutil.which("bash"), "нет bash")
class LabShiftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "run" / "brain").mkdir(parents=True)
        (self.root / "run" / "supervisor").mkdir(parents=True)
        (self.root / "logs").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def lab(self, *cmd, running, states, limit="2", bots="bot01 bot02 bot03 bot04"):
        env = {"PATH": "/usr/bin:/bin", "LAB_ROOT": str(self.root), "LAB_SCRIPT": str(LAB),
               "LAB_MAX_ONLINE": limit, "LAB_BOTS": bots, "RUNNING_BOTS": running, "STATES": states}
        return subprocess.run(["bash", "-c", HARNESS, "harness", *cmd], env=env, capture_output=True,
                              text=True, timeout=30)

    def inbox(self, bot):
        f = self.root / "run" / "brain" / f"{bot}.inbox"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def actions(self):
        f = self.root / "actions"
        return f.read_text().split("\n")[:-1] if f.exists() else []

    def test_over_limit_asks_sleepers_first_then_limit(self):
        r = self.lab("shift_balance", running="bot01 bot02 bot03 bot04",
                     states="bot01:awake bot02:awake bot03:limit bot04:asleep")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.inbox("bot01") + self.inbox("bot02"), [], "бодрствующих смены не трогаем")
        self.assertEqual(self.inbox("bot04")[0]["cmd"], "sleep", "спящий по хронотипу — первым")
        self.assertEqual(self.inbox("bot03")[0]["hours"], 2)
        self.assertEqual(self.actions(), [], "никого не останавливаем, пока не уснул")
        r = self.lab("shift_balance", running="bot01 bot02 bot03 bot04",
                     states="bot01:awake bot02:awake bot03:limit bot04:asleep")
        self.assertEqual(len(self.inbox("bot04")), 1, "повтор просьбы — не раньше чем через 30 мин")

    def test_asleep_flag_frees_process_and_blocks_start(self):
        (self.root / "run" / "brain" / "bot03.asleep").write_text(f"{int(time.time()) + 3600}\n")
        r = self.lab("shift_balance", running="bot01 bot02 bot03",
                     states="bot01:awake bot02:awake bot03:limit")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.actions(), ["STOP bot03"])
        self.assertEqual(self.inbox("bot03"), [], "уснул — просить не нужно")
        r = self.lab("may_start_bot", "bot03", "bot01 bot02 bot03", running="bot01", states="")
        self.assertNotEqual(r.returncode, 0, "спящего по флагу не будим")
        (self.root / "run" / "brain" / "bot03.asleep").write_text(f"{int(time.time()) - 60}\n")
        r = self.lab("may_start_bot", "bot03", "bot01 bot02 bot03", running="bot01", states="")
        self.assertEqual(r.returncode, 0, "час пробуждения прошёл — можно поднять")

    def test_within_limit_and_without_limit_nothing(self):
        r = self.lab("shift_balance", running="bot01 bot02", states="bot01:awake bot02:awake")
        self.assertEqual((r.returncode, self.actions(), self.inbox("bot02")), (0, [], []))
        r = self.lab("shift_balance", limit="", running="bot01 bot02 bot03 bot04", states="")
        self.assertEqual((r.returncode, self.actions(), self.inbox("bot04")), (0, [], []))


if __name__ == "__main__":
    unittest.main()
