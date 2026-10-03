"""Агентный контракт: устойчивое намерение, реальные итоги, единый исполнитель."""
import asyncio
import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from live_brain.activity import Activities
from live_brain.config import Settings
from live_brain.gate import RuleGate
from live_brain.memory import Memory
from live_brain.mind import Mind
from live_brain.routine import load_world

ROOT = Path(__file__).resolve().parents[1]


class AgencyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sent = []
        async def send(action):
            self.sent.append(dict(action))
            return len(self.sent)
        persona = json.loads((ROOT / 'personas/bot01.json').read_text())
        # Синтетический сценарий Payon не зависит от текущей миссии deployment persona.
        persona['hunt_maps'] = ['pay_dun00']
        persona.pop('sleep', None)
        self.mem = Memory(self.root / 'memory.sqlite')
        self.m = Mind(Settings.from_env({'BRAIN_AGENCY': '1'}), persona, self.mem, send,
                      self.root / 'decisions.jsonl', RuleGate(), peers={'Arkady', 'Vera'},
                      world=load_world(ROOT / 'world/goals.json'))
        self.m.calendar = self.m.tradition = None
        self.now = time.time()
        self.a = Activities(self.m, clock=lambda: self.now, rng=random.Random(7))
        self.m.activities = self.a
        self.m.state = {'map': 'payon', 'x': 161, 'y': 58, 'lock_map': 'payon',
                        'hp_pct': 100, 'weight_pct': 45, 'zeny': 60000, 'items': {'501': 4},
                        'players': [{'name': 'Vera', 'x': 164, 'y': 58}], 'activity': 'idle'}
        self.m.fresh_state = True
        self.m.state_received = time.time()
        self.m.routine.new_day(self.now)
        self.m.routine.st.update(mode='town', arrived=True, rest_until=self.now + 3600)

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def run_async(self, operation):
        return asyncio.run(operation)

    def test_pending_service_is_not_replaced_by_new_social_score(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        started = self.a.st['since']
        commands = len(self.sent)
        self.m.needs.weighted = lambda: {'social': 2, 'care': 2, 'supply': .3, 'progress': 0}
        self.now += 2
        self.a.next_decide = 0
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st['name'], 'service', 'занятие не закончено: не отвлекаться')
        self.assertEqual(self.a.st['since'], started)
        self.assertEqual(len(self.sent), commands, 'не посылать конкурирующее движение')

    def test_stale_snapshot_cannot_complete_service(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.m.fresh_state = False
        self.m.state['items']['501'] = 25
        self.m.state['weight_pct'] = 20
        self.now += 2
        self.run_async(self.a.tick())
        self.assertIsNone(self.a.st.get('proved'), 'старое состояние не доказывает результат')

    def test_service_timeout_is_remembered_with_backoff(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.now = self.a.st["deadline"] + 1
        self.a.next_decide = self.now + 30
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'failed')
        history = self.mem.get('agency_history', [])
        self.assertEqual(history[-1]['name'], 'service')
        self.assertEqual(history[-1]['status'], 'failed')
        self.assertIn('deadline', history[-1]['why'])
        self.assertGreater(self.a.st['avoid_until']['service'], self.now)

    def test_llm_body_action_cannot_interrupt_service_but_speech_can(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.run_async(self.m.execute([{'action': 'set_hunt_map', 'map': 'pay_dun00'},
                                      {'action': 'say', 'text': 'Сначала закончу закупку.'}],
                                     source='llm', reason='конкурирующее решение'))
        kinds = [a['action'] for a in self.sent]
        self.assertNotIn('set_hunt_map', kinds)
        self.assertIn('say', kinds)

    def test_social_movement_cannot_steal_body_during_service(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.run_async(self.m.execute([{'action': 'meet_point', 'map': 'payon', 'x': 170, 'y': 58}],
                                     source='social', reason='прогулка', protocol=True))
        self.assertNotIn('meet_point', [a['action'] for a in self.sent])

    def test_rest_requires_actual_time_and_healthy_body(self):
        self.run_async(self.a.start('rest', self.now, self.m.state))
        self.a.next_decide = self.now + 1000
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'running')
        self.now += 61
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'succeeded')

    def test_hunting_requires_new_combat_evidence_not_only_arrival(self):
        self.m.routine.st.update(mode='hunt', mode_since=self.now)
        self.m.state.update(map='pay_dun00', lock_map='pay_dun00')
        self.run_async(self.a.start('keep_hunting', self.now, self.m.state))
        self.a.next_decide = self.now + 1000
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'running')
        self.mem.add_event('kill', {'monster': 'Skeleton'})
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'succeeded')

    def test_service_must_finish_npc_work_before_claiming_success(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.a.next_decide = self.now + 1000
        self.m.state.update(activity='buyAuto', weight_pct=20, items={'501': 25})
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'running', 'NPC ещё занят')
        self.m.state['activity'] = 'idle'
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'succeeded')

    def test_llm_can_propose_only_an_available_catalog_intent(self):
        self.m.state.update(weight_pct=20, items={'501': 25})
        self.run_async(self.m.apply({'intent': {'activity': 'hunt_early', 'why': 'Накоплю на оружие.'},
                                    'actions': []}, 'выбор занятия', 0, {}))
        self.assertEqual(self.a.st.get('name'), 'hunt_early')
        self.assertEqual(self.a.st.get('chosen_by'), 'llm')
        self.assertIn('оружие', self.a.st.get('reason', ''))

    def test_routine_schedule_cannot_leave_during_service(self):
        self.m.routine.st['rest_until'] = self.now - 1
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.run_async(self.m.routine.in_town(self.now, self.m.state))
        self.assertEqual(self.m.routine.st['mode'], 'town')

    def test_follower_finishes_own_service_before_following_leader(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.run_async(self.m.routine.follow_lead(self.now, self.m.state, ('hunt', 'pay_dun00')))
        self.assertEqual(self.m.routine.st['mode'], 'town', 'лидер не отменяет незавершённую закупку')

    def test_apply_cannot_bypass_intent_by_changing_routine_preference(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        before = self.m.routine.st.get('prefer_map')
        self.run_async(self.m.apply({'actions': [{'action': 'set_hunt_map', 'map': 'pay_dun00'}]},
                                   'обход замысла', 0, {}))
        self.assertEqual(self.m.routine.st.get('prefer_map'), before)

    def test_death_cancels_intent_instead_of_claiming_progress(self):
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.m.state.update(dead=True, items={'501': 25}, weight_pct=20)
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'cancelled')
        self.assertFalse(self.a.st.get('proved'))

    def test_operator_hunt_overrides_intent_but_keeps_health_gate(self):
        self.m.state.update(items={'501': 25}, weight_pct=20)
        self.run_async(self.a.start('rest', self.now, self.m.state))
        why = self.run_async(self.m.routine.force('hunt'))
        self.assertIsNone(why)
        self.assertEqual(self.m.routine.st['mode'], 'hunt')
        self.assertEqual(self.a.st['status'], 'cancelled')
        self.run_async(self.m.routine.force('rest'))
        self.m.state['hp_pct'] = 20
        self.assertIsNotNone(self.run_async(self.m.routine.force('hunt')))
        self.assertEqual(self.m.routine.st['mode'], 'town')

    def test_operator_rest_is_not_undone_by_selection_or_leader(self):
        self.run_async(self.m.routine.force('rest'))
        self.run_async(self.a.tick())
        self.assertNotEqual(self.a.st.get('status'), 'running')
        self.run_async(self.m.routine.follow_lead(self.now, self.m.state, ('hunt', 'pay_dun00')))
        self.assertEqual(self.m.routine.st['mode'], 'town')

    def test_routine_does_not_pull_social_visit_back_to_anchor(self):
        self.m.state['players'] = [{'name': 'Vera', 'x': 175, 'y': 58, 'map': 'payon'}]
        self.run_async(self.a.start('socialize', self.now, self.m.state))
        before = len(self.sent)
        self.m.state.update(lock_map='payon', lock_x=175, lock_y=58)
        self.m.routine.last_sent = 0
        self.run_async(self.m.routine.enforce(self.now, self.m.state))
        self.assertEqual(len(self.sent), before, 'routine не должна отменять поход к знакомому')

    def test_leaving_hunt_cancels_pending_hunt_proof(self):
        self.m.routine.st['mode'] = 'hunt'
        self.m.state['map'] = 'pay_dun00'
        self.run_async(self.a.start('keep_hunting', self.now, self.m.state))
        self.run_async(self.m.routine.to_town(self.now))
        self.m.state['map'] = 'payon'
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st['status'], 'cancelled')
        self.assertEqual(self.mem.get('agency_history')[-1]['name'], 'keep_hunting')

    def test_chain_does_not_advance_after_failed_service_proof(self):
        self.m.state.update(weight_pct=55, items={'501': 0})
        self.run_async(self.a.start('service', self.now, self.m.state))
        self.a.st['chain'] = {'target': 'socialize', 'steps': [
            {'name': 'service', 'fixes': {'light': True}, 'started': self.now,
             'deadline': self.a.st['deadline']}], 'i': 0, 'mode': 'town',
             'since': self.now, 'until': self.now + 3600, 'missing': {'light': True}}
        self.m.state['weight_pct'] = 45
        self.now = self.a.st['deadline'] + 1
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st['name'], 'service')
        self.assertEqual(self.a.st['status'], 'failed')
        self.assertIsNone(self.a.st.get('chain'))
        self.assertNotIn('chain_done', self.m.decisions_path.read_text())

    def test_llm_choice_is_event_driven_with_bounded_local_fallback(self):
        self.m.s.llm_provider, self.m.s.api_key = 'openrouter', 'unit-test-only'
        self.run_async(self.a.tick())
        self.assertIsNone(self.a.st.get('name'), 'сначала предложить модели выбрать допустимый intent')
        self.assertEqual(self.m.reasons[-1]['kind'], 'event')
        self.now += 31
        self.run_async(self.a.tick())
        self.assertEqual(self.a.st.get('status'), 'running', 'не зависать без ответа модели')
        self.assertEqual(self.a.st['chosen_by'], 'local')

    def test_agency_does_not_spend_llm_on_periodic_timer(self):
        self.m.s.llm_provider, self.m.s.api_key = 'openrouter', 'unit-test-only'
        async def quiet_tick(mind):
            return None
        from unittest.mock import patch as mock_patch
        self.m.peer_smalltalk = lambda now: None
        self.m.last_decision = 0
        with mock_patch.object(self.m.registry, "tick", new=quiet_tick):
            self.assertIsNone(self.run_async(self.m.step_rules()))

    def test_completed_intent_respects_its_cooldown(self):
        self.assertGreater(self.a.catalog['rest']['cooldown_minutes'], 0)
        self.run_async(self.a.start('rest', self.now, self.m.state))
        self.now += 61
        self.run_async(self.a.check_proof(self.now, self.m.state))
        self.assertEqual(self.a.st['status'], 'succeeded')
        self.assertNotIn('rest', self.a.scores(self.m.state)[0], 'не повторять уже выполненный отдых до cooldown')






