import copy
import hashlib
import random
import unittest
from datetime import datetime, timezone

from packages.resident_behavior.config import load_behavior


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.config = load_behavior('config/behavior.yaml')
        self.now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        self.snapshot = {'version':1,'resident_id':'ResidentA','seq':1,'server_time':self.now.isoformat(), 'map':'prontera','x':110,'y':185,'hp':90,'hp_max':100,'weight':0,'weight_max':100,'inventory':{'501':8},'alive':True,'near_players':[],'near_monsters':[]}
        self.mental = {'drives':dict(self.config['residents']['resident_a']['initial_drives']), 'mood':{k:0 for k in self.config['mood']['states']}, 'goals':[], 'relations':{'Novi':{'familiarity':20,'liking':10,'trust':30}}, 'derived':dict.fromkeys('sleeping chat_due help_request meet_due break_due novelty habit_due favorite_place speech_cooldown recovery_lock hunt_streak night_awake morning_awake'.split(), 0), 'rng_index':7}
        self.mental['derived'].update(rest_safe=1,service_need=0,service_possible=1,meet_available=1,player='Novi',whisper_target='Novi',resident='mira',place='square',player_valid=1,whisper_target_valid=1,resident_valid=1,place_valid=1)

    def test_repeat_penalty_counts_occurrences_only_in_last_three(self):
        from packages.resident_behavior.engine import rank_candidates
        for history in (['idle', 'hunt', 'idle'],
                        ['idle', 'idle', 'hunt', 'idle', 'rest'],
                        [{'action': 'idle'}, {'action': 'hunt'}, {'action': 'idle'}]):
            self.mental['last_actions'] = history
            # Persisted legacy counts must not override authoritative recent history.
            self.mental['repetitions'] = {'idle': 99, 'hunt': 99}
            result = rank_candidates(self.config, 'resident_a', self.snapshot, self.mental, self.now)
            recent = [h.get('action') if isinstance(h, dict) else h for h in history[-3:]]
            for entry in result['utilities']:
                self.assertEqual(entry['repeat_penalty'], 12 * recent.count(entry['action']))
        self.mental['derived']['sleeping'] = 1
        result = rank_candidates(self.config, 'resident_a', self.snapshot, self.mental, self.now)
        self.assertTrue(all(u['repeat_penalty'] == 0 for u in result['utilities']))

    def test_unknown_monsters_veto_rest_and_history_repetition(self):
        from packages.resident_behavior.engine import rank_candidates
        snap = dict(self.snapshot, near_monsters=None)
        result = rank_candidates(self.config,'resident_a',snap,self.mental,self.now.timestamp())
        self.assertIn('rest',[v['action'] for v in result['vetoes']])
        self.mental['last_actions']=['hunt','idle','idle']
        result = rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        idle = next(u for u in result['utilities'] if u['action']=='idle')
        self.assertEqual(idle['repeat_penalty'],24)
        self.mental['mood']['scared']=20
        result = rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        rest = next(u for u in result['utilities'] if u['action']=='rest')
        self.assertEqual(rest['mood_terms'],{'scared':5})
        self.assertAlmostEqual(rest['total'],sum(rest['drive_terms'].values())+sum(rest['mood_terms'].values())-rest['repeat_penalty'])

    def test_numeric_actual_bools_are_unknown_but_flags_remain_valid(self):
        from packages.resident_behavior.engine import rank_candidates
        fields = 'hp hp_max sp sp_max weight weight_max zeny base_level job_level free_skill_points online_minutes'.split()
        for field in fields:
            for value in (True, False):
                config = copy.deepcopy(self.config)
                config['actions']['town_service']['utility'] = field
                snap = dict(self.snapshot, **{field: value})
                result = rank_candidates(config, 'resident_a', snap, self.mental, self.now)
                with self.subTest(field=field, value=value):
                    self.assertIn('town_service', [v['action'] for v in result['vetoes']])
        for field, value in [('hp', True), ('hp_max', True), ('weight', False),
                             ('weight_max', True), ('inventory', {'501': True})]:
            result = rank_candidates(self.config, 'resident_a', dict(self.snapshot, **{field: value}), self.mental, self.now)
            self.assertIn('hunt', [v['action'] for v in result['vetoes']])
        for field in ('x', 'y'):
            for value in (True, False):
                result = rank_candidates(self.config, 'resident_a', dict(self.snapshot, **{field: value}), self.mental, self.now)
                self.assertIn('walk_to', [v['action'] for v in result['vetoes']])
        config = copy.deepcopy(self.config)
        config['actions']['town_service']['utility'] = 'alive+sitting'
        result = rank_candidates(config, 'resident_a', dict(self.snapshot, sitting=False), self.mental, self.now)
        self.assertNotIn('town_service', [v['action'] for v in result['vetoes']])

    def test_actual_fields_cannot_be_replaced_by_derived_guesses(self):
        from packages.resident_behavior.engine import rank_candidates
        self.config['actions']['town_service']['utility']='zeny'
        self.snapshot['zeny']=None
        self.mental['derived']['zeny']=870
        result=rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertIn('town_service',[v['action'] for v in result['vetoes']])

    def test_ties_softmax_walk_priority_and_seed_utc(self):
        from packages.resident_behavior.engine import rank_candidates, rng_seed
        from datetime import timedelta
        for action in self.config['actions']:
            self.config['actions'][action]['utility']='1000000'
            self.config['mood']['utility_modifiers'][action]='0'
        result=rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertEqual([c['action'] for c in result['candidates']],['hunt','idle','meet'])
        for c in result['candidates']: self.assertAlmostEqual(c['p'],1/3)
        self.mental['derived'].update(meet_due=1,habit_due=1,habit_place='kafra',meet_place='square')
        result=rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='walk_to')['args']['place'],'square')
        self.mental['derived']['meet_due']=0
        result=rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='walk_to')['args']['place'],'kafra')
        self.assertEqual(rng_seed('a',self.now,1,'selection'),rng_seed('a',self.now.astimezone(timezone(timedelta(hours=-8))),1,'selection'))
        self.config['actions']['hunt']['utility']='1e308'
        self.config['mood']['utility_modifiers']['hunt']='1e308'
        result=rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertIn('hunt',[v['action'] for v in result['vetoes']])

    def test_rank_and_audit_formula(self):
        from packages.resident_behavior.engine import rank_candidates
        from packages.resident_behavior.expressions import evaluate
        result = rank_candidates(self.config, 'resident_a', self.snapshot, self.mental, self.now)
        self.assertEqual(len(result['utilities']), 9)
        self.assertEqual(len(result['candidates']), 3)
        self.assertAlmostEqual(sum(c['p'] for c in result['candidates']), 1)
        hunt = next(u for u in result['utilities'] if u['action']=='hunt')
        context = dict(self.mental['derived'], **self.mental['drives'], **self.config['residents']['resident_a']['traits'])
        expected = evaluate(self.config['actions']['hunt']['utility'], context)
        neutral = evaluate(self.config['actions']['hunt']['utility'], dict(context, **dict.fromkeys(self.config['traits'], .5)))
        self.assertAlmostEqual(hunt['total'], expected)
        self.assertAlmostEqual(hunt['trait_terms'], expected-neutral)
        self.assertEqual(hunt['args']['map'], 'prt_fild08')
        self.assertTrue(10 <= hunt['args']['minutes'] <= 20)
        self.assertEqual([c['action'] for c in result['candidates']], [c['action'] for c in sorted(result['candidates'], key=lambda c:(-c['total'], c['action']))])

    def test_unknown_and_unsafe_telemetry_veto(self):
        from packages.resident_behavior.engine import rank_candidates
        for key, value in [('hp',None),('inventory',None),('near_monsters',None),('weight',None),('alive',None),('near_monsters',[{'id':999,'x':1,'y':1}])]:
            snap = dict(self.snapshot); snap[key] = value
            result = rank_candidates(self.config, 'resident_a', snap, self.mental, self.now)
            self.assertIn('hunt', [v['action'] for v in result['vetoes']])
        result = rank_candidates(self.config, 'resident_a', {}, {}, self.now)
        self.assertEqual([c['action'] for c in result['candidates']], ['idle'])
        self.assertEqual(result['candidates'][0]['args'], {'minutes':1})

    def test_rng_primary_then_selected_args_then_override(self):
        from packages.resident_behavior.engine import select, rng_seed
        from packages.resident_behavior.contracts import validate_execute, validate_telemetry
        from tests.test_behavior_contracts import execute, snapshot, TIME
        for action, potions in [('hunt', 8), ('hunt', 0), ('idle', 8)]:
            for spec in self.config['actions'].values():
                spec['utility'] = '0'
            self.config['actions'][action]['utility'] = '1000000'
            self.mental['rng_index'] = 9
            self.snapshot['inventory'] = {'501': potions}
            rng = random.Random(rng_seed('resident_a', self.now, 9, 'selection'))
            rng.random()  # primary
            minutes = rng.uniform(10, 20) if action == 'hunt' and potions else 5
            override = rng.random() < .1
            result = select(self.config, 'resident_a', self.snapshot, self.mental, self.now)
            with self.subTest(action=action, potions=potions):
                self.assertEqual(result['primary']['action'], action)
                self.assertEqual(result['primary']['args']['minutes'], minutes)
                self.assertEqual(result['override_allowed'], override)
                self.assertEqual(result['selected'], result['primary'])
                payload = execute(action, result['primary']['args'])
                self.assertEqual(validate_execute(payload), payload)
                observation = snapshot()
                observation['current_action'] = dict(action_id='a1', action=action,
                    args=result['primary']['args'], started_at=TIME, result=None, error_code=None)
                self.assertEqual(validate_telemetry(observation), observation)
                for key in ('candidates', 'utilities'):
                    self.assertEqual(next(c for c in result[key] if c['action'] == action)['args'], result['primary']['args'])
                if action == 'hunt' and potions:
                    self.assertEqual(minutes, 10.283352323250373)
                    self.assertFalse(override)
                elif action == 'idle':
                    # Non-primary hunt uses valid default args, never consumes a draw.
                    self.assertEqual(next(u for u in result['utilities'] if u['action'] == 'hunt')['args']['minutes'], 10)

    def test_selection_seed_replay_no_mutation(self):
        from packages.resident_behavior.engine import select
        before = copy.deepcopy(self.mental)
        result = select(self.config, 'resident_a', self.snapshot, self.mental, self.now)
        seed = int.from_bytes(hashlib.sha256(b'resident_a|2026-10-10|7|selection').digest()[:8], 'big')
        self.assertEqual(result['seed'], seed)
        self.assertEqual(result['rng_index'], 7)
        self.assertEqual(result['next_rng_index'], 8)
        rng = random.Random(seed); roll = rng.random(); cumulative=0
        for candidate in result['candidates']:
            cumulative += candidate['p']
            if roll < cumulative: break
        self.assertEqual(result['primary'], candidate)
        self.assertEqual(result['selected'], result['primary'])
        self.assertEqual(result, select(self.config,'resident_a',self.snapshot,self.mental,self.now))
        self.assertEqual(self.mental,before)
        single = select(self.config,'resident_a',{}, {},self.now)
        self.assertFalse(single['llm_required']); self.assertFalse(single['override_allowed'])

    def test_dynamic_args_and_repeat_sleep(self):
        from packages.resident_behavior.engine import rank_candidates
        self.mental['derived'].update(help_request=1)
        result = rank_candidates(self.config,'borin',self.snapshot,self.mental,self.now)
        follow = next(u for u in result['utilities'] if u['action']=='follow')
        self.assertEqual(follow['args']['minutes'],15)
        self.mental['relations']['Novi']['familiarity']=0
        result = rank_candidates(self.config,'borin',self.snapshot,self.mental,self.now)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='follow')['args']['minutes'],5)
        self.snapshot['inventory']={}
        result = rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='hunt')['args']['minutes'],5)
        self.mental['repetitions']={'idle':4}
        result = rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='idle')['repeat_penalty'],36)
        self.mental['derived'].update(sleeping=1)
        result = rank_candidates(self.config,'resident_a',self.snapshot,self.mental,self.now)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='idle')['repeat_penalty'],0)
        self.assertEqual(next(u for u in result['utilities'] if u['action']=='walk_to')['args']['place'],'home_a')
