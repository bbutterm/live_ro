"""Offline prerequisite evidence tests; no sockets or live database access."""
import json
import tempfile
import time
import unittest

from packages.body_gateway.executor import Executor
from packages.body_gateway.journal import Journal


class BehaviorGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.j = Journal(self.tmp.name + '/world.sqlite')
        self.addCleanup(self.j.close)
        self.sent = []
        self.e = Executor(self.j, 'resident_a', 150001, self.sent.append)
        self.e.body({'type': 'hello', 'body_epoch': 'ep'})
        self.telemetry()

    def telemetry(self, **updates):
        frame = dict(type='telemetry', body_epoch='ep', ts=time.time(),
                     map='prt_in', pos={'x': 126, 'y': 76}, dead=False,
                     running=None, inventory={'909': 20, '713': 10, '938': 5},
                     hp=90, hp_max=100, behavior_v1=True, sitting=True)
        frame.update(updates)
        self.e.body(frame)

    def done(self, a, code='SOLD'):
        self.e.body(dict(type='skill_result', action_id=a['action_id'],
                         outcome='done', code=code))

    def trade(self, iid, amount, rid=1, **updates):
        row = dict(id=rid, char_id=150001, type='S', map='prt_in',
                   item_id=iid, amount=amount, event_ts=time.time())
        row.update(updates)
        self.j.event('picklog', rid, row['event_ts'], row, rid)

    def test_explicit_items_are_strictly_validated_before_dispatch(self):
        invalid = [None, [], {}, {'914': 1}, {909: 1}, {'0909': 1},
                   {'909': True}, {'909': False}, {'909': 0}, {'909': -1},
                   {'909': 1.0}, {'909': '1'}, {'909': 1001},
                   {'909': 21}, {'713': 11}, {'938': 6}]
        for items in invalid:
            with self.subTest(items=items):
                with self.assertRaisesRegex(ValueError, 'INVALID_ITEMS'):
                    self.e.run('sell_loot', {'items': items}, 60)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.j.actions(), [])

    def test_v1_requires_explicit_items(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_ITEMS'):
            self.e.run('sell_loot', {}, 60)
        self.assertEqual(self.sent, [])

    def test_partial_sale_confirms_exact_plan_not_whole_stacks(self):
        a = self.e.run('sell_loot', {'items': {'909': 3, '713': 2, '938': 1}}, 60, 'partial')
        self.done(a)
        self.telemetry(inventory={'909': 17, '713': 8, '938': 4})
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.trade(909, -3)
        self.trade(713, -2, 2)
        self.trade(938, -1, 3)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')
        evidence = json.loads(self.j.trace(a['action_id'])[-1]['evidence'])
        self.assertEqual(len(evidence['server_picklog']), 3)
        self.assertEqual(self.e.run('sell_loot', {}, 60, 'partial')['action_id'], a['action_id'])
        self.assertEqual(len(self.sent), 1)

    def test_sale_requires_exact_picklog_quantity(self):
        for quantity in (2, 4):
            with self.subTest(quantity=quantity):
                a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
                self.done(a)
                self.telemetry(inventory={'909': 0})
                self.trade(909, -quantity, quantity)
                self.e.evaluate()
                self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
                self.j.transition(a['action_id'], 'cancelled')
                self.telemetry()

    def position(self, rid=1):
        self.e.witness(dict(id=rid, kind='pos', char_id=150001,
                            map='prt_in', x=126, y=76, event_ts=time.time()))

    def test_v1_rest_requires_literal_sitting_true(self):
        a = self.e.run('rest', {'hp_pct': 85}, 60)
        self.done(a, 'RESTED')
        self.position()
        for sitting in (False, None, 1, 'true'):
            with self.subTest(sitting=sitting):
                self.telemetry(sitting=sitting)
                self.e.evaluate()
                self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.telemetry(sitting=True)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_legacy_rest_still_confirms_standing(self):
        self.telemetry(behavior_v1=False, sitting=False)
        a = self.e.run('rest', {'hp_pct': 85}, 60)
        self.done(a, 'RESTED')
        self.position()
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_explicit_sale_cannot_confirm_across_body_epoch(self):
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.done(a)
        self.trade(909, -3)
        self.e.body({'type': 'hello', 'body_epoch': 'new-ep'})
        self.telemetry(body_epoch='new-ep', inventory={'909': 17})
        self.e.evaluate()
        self.assertNotEqual(self.j.action(a['action_id'])['state'], 'confirmed')
        self.assertEqual(len(self.sent), 1)

    def test_legacy_sale_without_items_keeps_whole_whitelist(self):
        self.telemetry(behavior_v1=False, inventory={'909': 20, '914': 2, '713': 10})
        a = self.e.run('sell_loot', {}, 60)
        self.done(a)
        self.telemetry(behavior_v1=False, inventory={'909': 17, '914': 0, '713': 10})
        self.trade(909, -3)
        self.trade(914, -2, 2)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.trade(909, -17, 3)
        self.telemetry(behavior_v1=False, inventory={'713': 10})
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_legacy_sale_also_requires_junk_reported_sellable_by_type(self):
        # E05: weapons/etc outside the fixed list are sold too, and must be proven too.
        self.telemetry(behavior_v1=False, inventory={'909': 3, '1201': 2, '501': 9}, sellable={'909': 3, '1201': 1})
        a = self.e.run('sell_loot', {}, 60)
        self.done(a)
        self.telemetry(behavior_v1=False, inventory={'1201': 2, '501': 9}, sellable={'1201': 1})
        self.trade(909, -3)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.trade(1201, -1, 2)
        self.telemetry(behavior_v1=False, inventory={'1201': 1, '501': 9}, sellable={})
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_sale_requires_body_inventory_reduction_for_every_item(self):
        a = self.e.run('sell_loot', {'items': {'909': 3, '713': 2}}, 60)
        self.done(a)
        self.trade(909, -3)
        self.trade(713, -2, 2)
        for inventory in ({'909': 18, '713': 8}, {'909': 17, '713': 9}):
            with self.subTest(inventory=inventory):
                self.telemetry(inventory=inventory)
                self.e.evaluate()
                self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.telemetry(inventory={'909': 16, '713': 7})
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_unplanned_sale_is_not_success(self):
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.done(a)
        self.telemetry(inventory={'909': 17})
        self.trade(914, -3)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.trade(909, -3, 2)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')

    def test_sale_excludes_picklog_at_or_before_baseline(self):
        self.trade(909, -3, 5)
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.done(a)
        self.telemetry(inventory={'909': 17})
        self.trade(909, -3, 4)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.trade(909, -3, 6)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_unrelated_picklog_is_not_evidence(self):
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.done(a)
        self.telemetry(inventory={'909': 17})
        cases = [dict(char_id=150002), dict(map='prontera'), dict(type='P'),
                 dict(event_ts=a['created']-2)]
        for rid, update in enumerate(cases, 1):
            self.trade(909, -3, rid, **update)
        self.trade(909, 3, 5)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.trade(909, -3, 6)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_split_picklog_quantities_sum_to_exact_plan(self):
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.done(a)
        self.telemetry(inventory={'909': 17})
        self.trade(909, -1)
        self.trade(909, -2, 2)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_sale_retains_fresh_alive_idle_map_guards(self):
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.done(a)
        self.trade(909, -3)
        for update in (dict(ts=time.time()-4), dict(dead=True),
                       dict(running={'action_id': a['action_id']}), dict(map='prontera')):
            with self.subTest(update=update):
                self.telemetry(inventory={'909': 17}, **update)
                self.e.evaluate()
                self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.telemetry(inventory={'909': 17})
        self.e.received = time.time()-4
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.telemetry(inventory={'909': 17})
        self.e.projection['server_dead'] = {'value': True}
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.e.projection['server_dead'] = {'value': False}
        self.e.projection['server_map'] = {'value': 'prontera'}
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.e.projection['server_map'] = {'value': 'prt_in'}
        self.e.projection['server_online'] = {'value': False}
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.e.projection['server_online'] = {'value': True}
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_uncertain_dispatch_and_restart_never_resend_sale(self):
        def uncertain(message):
            self.sent.append(message)
            raise OSError('offline simulated transport uncertainty')
        self.e.send = uncertain
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60, 'once')
        self.assertEqual(a['state'], 'unknown')
        self.e = Executor(self.j, 'resident_a', 150001, self.sent.append)
        self.e.body({'type': 'hello', 'body_epoch': 'ep'})
        self.telemetry(inventory={'909': 17})
        self.assertEqual(self.e.run('sell_loot', {}, 60, 'once')['action_id'], a['action_id'])
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'unknown')
        self.trade(909, -3)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')
        self.assertEqual(len(self.sent), 1)

    def test_sale_quantity_bounds_include_one_and_thousand(self):
        self.telemetry(inventory={'909': 1000, '713': 1})
        a = self.e.run('sell_loot', {'items': {'909': 1000, '713': 1}}, 60)
        self.assertEqual(a['state'], 'dispatched')
        self.assertEqual(self.sent[0]['params']['items'], {'909': 1000, '713': 1})

    def test_missing_or_invalid_inventory_cannot_authorize_sale(self):
        for inventory in (None, {}, {'909': True}, {'909': '20'}, {'909': 20.0}):
            with self.subTest(inventory=inventory):
                self.telemetry(inventory=inventory)
                with self.assertRaisesRegex(ValueError, 'INVALID_ITEMS'):
                    self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.assertEqual(self.sent, [])

    def test_v1_rest_still_requires_hp_and_server_position(self):
        a = self.e.run('rest', {'hp_pct': 85}, 60)
        self.done(a, 'RESTED')
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.position()
        self.telemetry(hp=84)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'verifying')
        self.telemetry(hp=85)
        self.e.evaluate()
        self.assertEqual(self.j.action(a['action_id'])['state'], 'confirmed')

    def test_speech_parallel_motor_and_single_speech_slot(self):
        motor = self.e.run('rest', {'hp_pct': 85}, 60)
        speech = self.e.run('whisper', {'to_name': 'Tester', 'text': 'Hello'}, 60)
        self.assertEqual(speech['state'], 'dispatched')
        second = self.e.run('say', {'text': 'Hello'}, 60)
        self.assertEqual(second['code'], 'BUSY')
        other = self.e.run('travel_to', {'map': 'prontera', 'r': 0}, 60)
        self.assertEqual(other['code'], 'BUSY')
        self.assertEqual(self.j.action(motor['action_id'])['state'], 'dispatched')
        self.assertEqual(len(self.sent), 2)

    def test_speech_evidence_confirms_while_motor_running(self):
        motor = self.e.run('rest', {'hp_pct': 85}, 60)
        speech = self.e.run('say', {'text': 'Hello'}, 60)
        self.done(speech, 'SPOKEN')
        now = time.time()
        self.j.event('chat', 1, now, dict(id=1, char_id=150001, type='O', text='Hello'), 1)
        self.telemetry(running={'action_id': motor['action_id']})
        self.e.evaluate()
        self.assertEqual(self.j.action(speech['action_id'])['state'], 'confirmed')
        self.assertEqual(self.j.action(motor['action_id'])['state'], 'dispatched')

    def test_stale_snapshot_cannot_authorize_sale(self):
        self.telemetry(ts=time.time()-4)
        a = self.e.run('sell_loot', {'items': {'909': 3}}, 60)
        self.assertEqual((a['state'], a['code']), ('rejected_local', 'STALE_TELEMETRY'))
        self.assertEqual(self.sent, [])
