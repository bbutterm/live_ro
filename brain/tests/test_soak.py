"""soak: короткий прогон долгого мира (tools/soak.py) и регрессии багов, найденных долгим прогоном (docs/SOAK.md).

Сам долгий прогон (7–14 суток, 4 жителя) в набор не входит: python3 tools/soak.py --days 7.
"""
import asyncio
import sys
import unittest
from pathlib import Path

from tests.test_integration import BodyMixin

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


class SoakSmokeTest(unittest.TestCase):
    """Сутки, 2 жителя, такт 5 с: мир-симулятор и метрики работают, инварианты целы, исключений нет."""

    def test_one_day_two_residents(self):
        import soak
        s = soak.Soak(days=1, residents=2, dt=5, quiet=True)     # dt <= 5: MAX_TICK_GAP распорядка
        self.addCleanup(s.cleanup)
        s.run()
        self.assertEqual([k for k in s.errors], [], s.error_samples)
        names = {n for _, n, _ in s.rows}
        self.assertEqual(names, {"Arkady", "Vera"})
        for _, name, r in s.rows:
            self.assertEqual(r["invariants"], [], name)
            self.assertGreater(r["kills"], 0, f"{name} охотился")
            self.assertGreater(r["actions"], 0)
        w = s.world_rows[-1][1]
        self.assertEqual(w["zeny_total"], w["zeny_expected"], "зени мира сходятся")


class PartyRestTest(BodyMixin, unittest.TestCase):
    """soak: лидер ушёл отдыхать ради участника ([party:recover:]) — занятие hunt_early не возвращает его на охоту
    через 5 с (дребезг hunt/town каждые 30 с: 23 действия за минуту в долгом прогоне)."""

    def test_leader_rests_for_member(self):
        m = self.mind
        vera = {"name": "Vera", "online": True, "map": "prontera", "x": 157, "y": 186, "hp_pct": 30}
        me = dict(vera, name="Arkady", x=156, y=185, hp_pct=100, leader=True)
        party = {"party": "LR_Arkady", "party_members": [me, vera]}
        self.state(**party)
        r = m.routine
        r.new_day(self.clock.t, keep_mode="hunt")
        r.st.update(mode="hunt", mode_since=self.clock.t, budget=4 * 3600, hunted=600, session_end=3600)
        asyncio.run(m.step())
        self.assertTrue(m.party.confirmed())
        asyncio.run(m.on_message({"type": "event", "kind": "chat_private", "from": "Vera", "text": "[party:recover:]",
                                  "ts": self.clock.t}))
        self.assertEqual(r.st["mode"], "town")
        before = len(self.sent)
        modes = []
        for _ in range(120):
            self.clock.t += 1
            m.activities.next_decide = 0
            self.state(map="prontera", x=r.town["x"], y=r.town["y"], lock_map="prontera", lock_x=r.town["x"],
                       lock_y=r.town["y"], **party)
            asyncio.run(m.step())
            modes.append(r.st["mode"])
        self.assertEqual(set(modes), {"town"}, "лидер отдыхает весь перерыв ради участника")
        self.assertNotIn("hunt", [a["action"] for a in self.sent[before:]])



class TogetherCapTest(BodyMixin, unittest.TestCase):
    """soak: «провели время вместе» (+1 в сутки) — не выше together_cap: за 10 суток долгого прогона все пары
    жителей уходили в +10 (максимум), отношения переставали различаться."""

    def spend_day(self):
        self.state()                                           # Vera рядом (players)
        for _ in range(31 * 12):
            self.clock.t += 5
            self.mind.social.together(self.clock.t, self.mind.state, 5)
        return self.mem.relation("Vera")["affinity"]

    def test_daily_together_stops_at_cap(self):
        cap = self.mind.social.cfg.get("together_cap", 4)       # ORG-096 (привыкание) — общий предел 4
        for _ in range(3):
            self.mem.update_relation("Vera", 2, "давняя подруга")
        self.assertEqual(self.mem.relation("Vera")["affinity"], 6)
        self.assertLessEqual(cap, 6, "выше предела «вместе» отношение не растёт")
        days = []
        for _ in range(4):
            days.append(self.spend_day())
            self.clock.t += 86400
        self.assertEqual(days, [6, 6, 6, 6])

    def test_below_cap_still_grows(self):
        self.mem.update_relation("Vera", 2, "знакомая")
        self.assertEqual(self.spend_day(), 3)



class SocialBurstTest(BodyMixin, unittest.TestCase):
    """soak: при четырёх жителях реакции веером (поздравить всех с уровнями после сна, приветы, соболезнования)
    давали 13+ шёпотов за минуту (replay.invariants). Сверх burst_per_minute реплики ждут в очереди и уходят позже."""

    def test_fan_out_is_spread_not_lost(self):
        self.state()
        social = self.mind.social
        cap = social.cfg.get("burst_per_minute", 4)
        said = []                                   # реплики social до safety (у safety свой лимит на адресата)
        orig = self.mind.execute

        async def execute(actions, **kw):
            said.extend((self.clock.t, a) for a in actions if a["action"] == "whisper")
            return await orig(actions, **kw)
        self.mind.execute = execute
        for topic in ("weather", "hello", "bye", "weather", "hello", "bye", "weather"):
            asyncio.run(social.say("Vera", topic, 3))
        self.assertEqual(len(said), cap)
        for _ in range(240):
            self.clock.t += 1
            asyncio.run(social.flush(self.clock.t))
        self.assertEqual(len(said), 7, "отложенные реплики ушли")
        self.assertEqual(social.queue, [])
        for i, (t, _) in enumerate(said):
            self.assertLessEqual(sum(1 for t2, _ in said if t <= t2 < t + 60), cap, f"окно с реплики {i}")


class EventScanTest(BodyMixin, unittest.TestCase):
    """soak: тик не должен дорожать с возрастом памяти (7 суток прогона: тик модуля episodes ×9, rumors с 0 до
    0,13 мс/тик). Курсор episodes стоял на последнем «своём» событии и каждый тик пересматривал все kill/loot после
    него; rumors.deaths_on шёл по индексу ts через все события с момента слуха (частичный индекс kind+ts не брался)."""

    def fill(self, n=300):
        rows = [(self.clock.t - 3600 + i, "kill", '{"monster": "Poring"}') for i in range(n)]
        self.mem.db.executemany("INSERT INTO events (ts, kind, data) VALUES (?, ?, ?)", rows)
        self.mem.db.commit()
        return self.mem.db.execute("SELECT MAX(id) FROM events").fetchone()[0]

    def test_episodes_cursor_skips_foreign_kinds(self):
        last = self.fill()
        ep = self.mind.episodes
        ep.next_tick = 0
        ep.tick(self.clock.t)
        self.assertEqual(self.mem.get("episodes_cursor"), last, "курсор — на конце, kill не пересматриваются")

    def test_rumor_death_count_uses_kind_index(self):
        self.fill()
        sqls = []
        self.mem.db.set_trace_callback(sqls.append)
        try:
            self.mind.rumors.deaths_on("prt_fild08", self.clock.t - 7 * 86400)
        finally:
            self.mem.db.set_trace_callback(None)
        sql = next(q for q in sqls if "death_report" in q)
        plan = " ".join(str(r[3]) for r in self.mem.db.execute("EXPLAIN QUERY PLAN " + sql))
        self.assertIn("events_kind_ts", plan)

    def test_needs_last_event_uses_kind_index(self):
        self.fill()
        sqls = []
        self.mem.db.set_trace_callback(sqls.append)
        try:
            self.mind.needs.since("level_up")
        finally:
            self.mem.db.set_trace_callback(None)
        sql = next(q for q in sqls if "level_up" in q)
        plan = " ".join(str(r[3]) for r in self.mem.db.execute("EXPLAIN QUERY PLAN " + sql))
        self.assertIn("events_kind_ts", plan)


if __name__ == "__main__":
    unittest.main()
