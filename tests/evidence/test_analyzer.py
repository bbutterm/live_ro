"""Все входы вымышлены (synthetic), включая моделирование runtime metadata.

synthetic=False используется только для теста условной ветки доверия к metadata;
ни один положительный unit test не является свидетельством игры.
"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from packages.evidence.analyzer import analyze

ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-10-08T09:00:10+00:00'


def synthetic_fixture():
    event = dict(id=10, ts='2026-10-08 12:00:00.000', char_id=42,
                 kind='pos', map='synthetic_map', x=10, y=20, a1=None, a2=None)
    metadata = dict(source_id='synthetic-generation-1', source_type='rathena_witness',
                    synthetic=True, db_timezone='Europe/Moscow',
                    window_start='2026-10-08T09:00:00Z', window_end='2026-10-08T09:00:01Z',
                    collected_at='2026-10-08T09:00:02Z', max_age_seconds=60,
                    id_start=10, id_end=10,
                    claims=[dict(type='event', event_id=10,
                                 expected=dict(kind='pos', char_id=42, x=10))])
    return [event], metadata


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.events, self.meta = synthetic_fixture()
        # Моделируется заявленная provenance, не настоящая игра.
        self.meta['synthetic'] = False

    def run_analysis(self, status, code=None, text=None, now=NOW):
        output = analyze(text if text is not None else '\n'.join(map(json.dumps, self.events)), self.meta, now=now)
        self.assertEqual(output['status'], status, output)
        if code:
            self.assertIn(code, [x['code'] for x in output['findings'] + output['claims']])
        if status != 'VERIFIED':
            self.assertNotIn('VERIFIED', [x['status'] for x in output['claims']])
        return output

    def test_declared_runtime_branch(self):
        self.run_analysis('VERIFIED', 'event_matches')

    def test_synthetic_never_game_proof(self):
        self.meta['synthetic'] = True
        self.run_analysis('UNVERIFIED', 'synthetic')

    def test_event_synthetic_marker(self):
        self.events[0]['synthetic'] = True
        self.run_analysis('UNVERIFIED', 'synthetic_event')

    def test_missing_each_metadata_field(self):
        original = copy.deepcopy(self.meta)
        for key in original:
            with self.subTest(key=key):
                self.meta = {k: v for k, v in original.items() if k != key}
                self.run_analysis('UNVERIFIED', 'metadata_missing')

    def test_empty(self):
        self.events = []
        self.run_analysis('UNVERIFIED', 'empty_events')

    def test_no_claims(self):
        self.meta['claims'] = []
        self.run_analysis('UNVERIFIED', 'empty_claims')

    def test_stale(self):
        self.run_analysis('UNVERIFIED', 'stale', now='2026-10-08T10:00:10Z')

    def test_collected_at_does_not_refresh_old_event(self):
        self.meta['collected_at'] = '2026-10-08T10:00:09Z'
        self.run_analysis('UNVERIFIED', 'stale', now='2026-10-08T10:00:10Z')

    def test_duplicate(self):
        self.events *= 2
        self.run_analysis('UNVERIFIED', 'duplicate')

    def test_conflicting_duplicate(self):
        self.events.append(dict(self.events[0], x=11))
        self.run_analysis('FAILED', 'duplicate_conflict')

    def test_descending_ids(self):
        self.events.insert(0, dict(self.events[0], id=11))
        self.meta['id_end'] = 11
        self.run_analysis('FAILED', 'id_order')

    def test_missing_ids_including_edges(self):
        for start, end in ((9, 10), (10, 11), (9, 11), (1, 2**63-1)):
            with self.subTest(start=start, end=end):
                self.meta.update(id_start=start, id_end=end)
                self.run_analysis('UNVERIFIED', 'id_gap')

    def test_missing_claim_event(self):
        self.meta['id_end'] = 11
        self.meta['claims'][0]['event_id'] = 11
        self.run_analysis('UNVERIFIED', 'evidence_missing')

    def test_contradiction(self):
        self.meta['claims'][0]['expected']['x'] = 99
        self.run_analysis('FAILED', 'contradiction')

    def test_nullable_payload(self):
        self.events[0]['x'] = None
        self.run_analysis('UNVERIFIED', 'evidence_insufficient')
        del self.meta['claims'][0]['expected']['x']
        self.run_analysis('VERIFIED')

    def test_missing_each_event_field(self):
        original = self.events[0].copy()
        for key in original:
            with self.subTest(key=key):
                self.events = [{k: v for k, v in original.items() if k != key}]
                self.run_analysis('FAILED', 'event_fields')

    def test_ack_source(self):
        self.meta['source_type'] = 'command_ack'
        self.run_analysis('UNVERIFIED', 'source')

    def test_ack_event(self):
        self.events[0]['kind'] = 'command_ack'
        self.run_analysis('FAILED', 'event_schema')

    def test_hp_and_a1_claims_unsupported(self):
        for field in ('hp', 'a1', 'a2', 'command_ack'):
            self.meta['claims'][0]['expected'] = dict(kind='pos', char_id=42, **{field: 10})
            self.run_analysis('UNVERIFIED', 'claim_fields')

    def test_body_claim_unsupported(self):
        self.meta['claims'] = [dict(type='T1', success=True)]
        self.run_analysis('UNVERIFIED', 'claim_unsupported')

    def test_timezone_not_guessed(self):
        for zone in ('SYSTEM', '', None, 3, 'Unknown/Zone'):
            self.meta['db_timezone'] = zone
            self.run_analysis('UNVERIFIED', 'timezone')

    def test_normalized_timestamp_conflict(self):
        self.events[0]['ts_utc'] = '2026-10-08T12:00:00Z'
        self.run_analysis('FAILED', 'normalized_time_conflict')
        self.events[0]['ts_utc'] = '2026-10-08T09:00:00Z'
        self.run_analysis('VERIFIED')

    def test_aware_db_time_rejected(self):
        self.events[0]['ts'] += '+03:00'
        self.run_analysis('UNVERIFIED', 'event_time')

    def test_dst_ambiguous_and_nonexistent(self):
        self.meta['db_timezone'] = 'Europe/Amsterdam'
        for ts in ('2026-10-25 02:30:00', '2026-03-29 02:30:00'):
            self.events[0]['ts'] = ts
            self.run_analysis('UNVERIFIED', 'event_time')

    def test_dst_normal_conversion(self):
        self.meta['db_timezone'] = 'Europe/Amsterdam'
        self.events[0]['ts'] = '2026-10-08 11:00:00'
        self.run_analysis('VERIFIED')

    def test_order_not_timestamp(self):
        self.meta.update(id_end=11, window_start='2026-10-08T08:59:59Z')
        self.events.append(dict(self.events[0], id=11, ts='2026-10-08 11:59:59'))
        self.run_analysis('VERIFIED')

    def test_outside_window(self):
        self.events[0]['ts'] = '2026-10-08 12:00:02'
        self.run_analysis('FAILED', 'event_window')

    def test_future_collection(self):
        self.meta['collected_at'] = '2026-10-08T10:00:00Z'
        self.run_analysis('FAILED', 'metadata_invalid')

    def test_naive_evaluation_time(self):
        self.run_analysis('UNVERIFIED', 'evaluation_time', now='2026-10-08T09:00:10')

    def test_naive_metadata_time(self):
        self.meta['window_start'] = '2026-10-08T09:00:00'
        self.run_analysis('FAILED', 'metadata_invalid')

    def test_malformed_json_redacted(self):
        output = self.run_analysis('FAILED', 'json', text='SECRET invalid JSON')
        self.assertNotIn('SECRET', json.dumps(output))

    def test_duplicate_json_keys(self):
        text = json.dumps(self.events[0]).replace('"id": 10', '"id": 10, "id": 10')
        self.run_analysis('FAILED', 'json', text=text)

    def test_bad_types(self):
        for field, value in [('id', True), ('char_id', '42'), ('x', False), ('a1', 1.5), ('kind', []), ('map', 12)]:
            with self.subTest(field=field):
                self.events, _ = synthetic_fixture()
                self.events[0][field] = value
                self.run_analysis('FAILED', 'event_schema')

    def test_metadata_invalid_types(self):
        original = copy.deepcopy(self.meta)
        for field, value in [('id_start', True), ('id_end', 9), ('synthetic', 'false'), ('max_age_seconds', float('nan')), ('max_age_seconds', 0), ('claims', {})]:
            with self.subTest(field=field):
                self.meta = dict(original, **{field: value})
                self.run_analysis('FAILED', 'metadata_invalid')

    def test_pure_and_deterministic(self):
        before = copy.deepcopy(self.meta)
        first = self.run_analysis('VERIFIED')
        self.assertEqual(first, self.run_analysis('VERIFIED'))
        self.assertEqual(before, self.meta)

    def test_cli_exit_codes_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            event_path, meta_path = Path(directory)/'events.jsonl', Path(directory)/'metadata.json'
            event_path.write_text(json.dumps(self.events[0]), encoding='utf-8')
            cmd = [sys.executable, '-m', 'packages.evidence', '--events', str(event_path), '--metadata', str(meta_path), '--now', NOW]
            for synthetic, code in ((False, 0), (True, 2)):
                self.meta['synthetic'] = synthetic
                meta_path.write_text(json.dumps(self.meta), encoding='utf-8')
                p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(p.returncode, code, p.stderr)
                self.assertIn('status', json.loads(p.stdout))
            meta_path.write_text('SECRET not json', encoding='utf-8')
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(p.returncode, 1)
            self.assertNotIn('SECRET', p.stdout + p.stderr)
            self.assertEqual(json.loads(p.stdout)['status'], 'FAILED')


if __name__ == '__main__':
    unittest.main()
