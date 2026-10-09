"""Native Perl call-seam diagnostics; fixtures, not live gameplay acceptance."""
import json
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]


class NativeHuntObservationTest(unittest.TestCase):
    def run_native(self, monsters, attack_body, ai_queue=None, list_body=None):
        code = r'''BEGIN {
package Globals; use Exporter 'import'; our @EXPORT=qw($char $charID $accountID $field $net $messageSender %config); our($char,$charID,$accountID,$field,$net,$messageSender); our %config; $INC{'Globals.pm'}=1;
package Plugins; sub register{} sub addHooks{1} sub delHooks{} $INC{'Plugins.pm'}=1;
package Utils; use Exporter 'import'; our @EXPORT_OK=qw(calcPosition); sub calcPosition{{x=>170,y=>240}} $INC{'Utils.pm'}=1;
package Network; sub IN_GAME(){5} $INC{'Network.pm'}=1;
package AI; our @queue; sub clear{} sub action{$queue[0]} sub inQueue{my @wanted=@_; for my $wanted(@wanted){for my $entry(@queue){return 1 if $entry eq $wanted}} return 0} $INC{'AI.pm'}=1;
package FakeList; sub getItems{JSON::PP::decode_json($ARGV[2])}
package FakeChar; sub attack{ATTACK_BODY}
}
require $ARGV[0];
$ENV{RESIDENT_RUN_DIR}=$ARGV[1]; $ENV{RESIDENT_ESTOP}=$ARGV[1].'/ESTOP';
$Globals::char=bless{hp=>795,hp_max=>795,pos_to=>{x=>170,y=>240}},'FakeChar';
$Globals::monstersList=bless{},'FakeList'; $Globals::field={baseName=>'prt_fild08'};
my $clock=1000; my @frames;
{no warnings 'redefine'; *residentBody::time=sub{$clock}; *residentBody::emit=sub{push @frames,[@_]};}
my $epoch=residentBody::telemetryFrame()->{body_epoch};
residentBody::command({proto=>1,type=>'skill_start',action_id=>'observed-hunt',idem_key=>'observed-hunt',expect_epoch=>$epoch,skill=>'hunt',params=>{map=>'prt_fild08',duration=>30,min_kills=>1},deadline_ts=>1100});
@AI::queue=@{JSON::PP::decode_json($ARGV[3])};
residentBody::pollSkill(); $clock=1002; residentBody::pollSkill();
my $during=residentBody::telemetryFrame(); $clock=1030; residentBody::pollSkill();
print JSON::PP::encode_json({during=>$during,frames=>\@frames});'''
        code = code.replace('ATTACK_BODY', attack_body)
        if list_body is not None:
            code = code.replace('JSON::PP::decode_json($ARGV[2])', list_body)
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ['perl', '-e', code, str(ROOT/'plugins/residentBody/residentBody.pl'),
                 directory, json.dumps(monsters), json.dumps(ai_queue or ['route'])], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_failed_target_reader_is_not_classified_as_observed_absence(self):
        for reader in ('die "fixture reader failure"', 'return undef'):
            with self.subTest(reader=reader):
                result = self.run_native([], 'die "unexpected attack"', list_body=reader)
                final = [f[1] for f in result['frames'] if f[0] == 'skill_result'][0]
                self.assertEqual((final['outcome'], final['code']), ('done', 'HUNTED'))

    def test_empty_selection_finishes_explicit_no_target_not_false_hunted(self):
        result = self.run_native([], 'die "unexpected attack"')
        final = [f[1] for f in result['frames'] if f[0] == 'skill_result'][0]
        self.assertEqual((final['outcome'], final['code']), ('error', 'NO_TARGET'))
        self.assertIn('samples=2 eligible_seen=0 attack_attempts=0', final['detail'])

    def test_empty_selection_result_reaches_terminal_gateway_without_fake_kill(self):
        import time
        from packages.body_gateway.journal import Journal
        from packages.body_gateway.executor import Executor
        result = self.run_native([], 'die "unexpected attack"')
        native = [f[1] for f in result['frames'] if f[0] == 'skill_result'][0]
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory + '/journal.sqlite')
            executor = Executor(journal, 'resident_a', 150001, lambda m: None)
            now = time.time()
            executor.body({'type': 'hello', 'body_epoch': 'fixture'})
            executor.body({'type': 'telemetry', 'ts': now, 'map': 'prt_fild08',
                           'pos': {'x': 170, 'y': 240}, 'dead': False, 'hp': 795})
            action = executor.run('hunt', {'map': 'prt_fild08', 'duration': 30, 'min_kills': 1}, 70, 'fixture-hunt')
            native['action_id'] = action['action_id']
            native['type'] = 'skill_result'
            executor.body(native)
            executor.evaluate()
            outcome = journal.action(action['action_id'])
            self.assertEqual((outcome['state'], outcome['code']), ('failed', 'NO_TARGET'))
            self.assertEqual(executor.active(), [])
            self.assertEqual(journal.db.execute("SELECT COUNT(*) FROM events WHERE source='witness'").fetchone()[0], 0)

    def test_no_eligible_target_is_observed_without_claiming_kills(self):
        result = self.run_native([{'ID':'pupa','nameID':1008},
                                  {'ID':'dead-poring','nameID':1002,'dead':True}],
                                 'die "unexpected attack"')
        expected = {'samples':2, 'eligible_seen':0, 'attack_attempts':0, 'attack_errors':0, 'attack_rejections':0}
        self.assertEqual(result['during'].get('hunt_observation'), expected)
        final = [frame[1] for frame in result['frames'] if frame[0]=='skill_result'][0]
        self.assertEqual(final['code'], 'NO_TARGET')
        self.assertEqual(final['outcome'], 'error')
        self.assertIn('samples=2 eligible_seen=0 attack_attempts=0 attack_errors=0', final['detail'])
        self.assertIn('native kills required', final['detail'])


    def test_queued_attack_approach_is_not_requeued_by_hunt_poll(self):
        # Pinned AI::Attack processes route/move before a queued attack.
        # Checking only AI::action() mistakes this productive approach for idle.
        for queue in (['route', 'attack'], ['move', 'route', 'attack'], ['attack']):
            with self.subTest(queue=queue):
                result = self.run_native([{'ID':'poring','nameID':1002}],
                                         'die "queued attack was duplicated"', queue)
                self.assertEqual(result['during']['hunt_observation'],
                                 {'samples':0, 'eligible_seen':0,
                                  'attack_attempts':0, 'attack_errors':0, 'attack_rejections':0})
                final = [f[1] for f in result['frames'] if f[0]=='skill_result'][0]
                self.assertEqual(final['code'], 'HUNTED')
                self.assertIn('native kills required', final['detail'])

    def test_native_attack_rejection_is_distinct_from_exception_and_kill(self):
        # Pinned Actor::attack returns undef when target pos/pos_to is absent.
        # A normal return without a queued attack is not an exception or success.
        result = self.run_native([{'ID':'poring','nameID':1002}], 'return undef')
        observation = result['during']['hunt_observation']
        self.assertEqual(observation.get('attack_rejections'), 2)
        self.assertEqual(observation['attack_attempts'], 2)
        self.assertEqual(observation['attack_errors'], 0)
        final = [f[1] for f in result['frames'] if f[0]=='skill_result'][0]
        self.assertIn('attack_rejections=2', final['detail'])
        self.assertEqual(final['code'], 'HUNTED')
        self.assertIn('native kills required', final['detail'])

    def test_rejected_first_target_does_not_starve_located_target(self):
        result = self.run_native([{'ID':'unlocated','nameID':1002},
                                  {'ID':'located','nameID':1002}],
                                 "return undef if $_[1] eq 'unlocated'; return 1")
        observation = result['during']['hunt_observation']
        self.assertEqual(observation['attack_attempts'], 4)
        self.assertEqual(observation['attack_rejections'], 2)
        self.assertEqual(observation['attack_errors'], 0)

    def test_rejection_fallback_is_capped_and_exception_stops_selection(self):
        targets = [{'ID':str(i),'nameID':1002} for i in range(10)]
        result = self.run_native(targets, 'return undef')
        self.assertEqual(result['during']['hunt_observation']['attack_attempts'], 6)
        result = self.run_native(targets, 'die "attack error"')
        self.assertEqual(result['during']['hunt_observation']['attack_attempts'], 2)
        self.assertEqual(result['during']['hunt_observation']['attack_errors'], 2)

    def test_native_attack_acceptance_is_not_reported_as_rejection_or_kill(self):
        result = self.run_native([{'ID':'poring','nameID':1002}], 'return 1')
        observation = result['during']['hunt_observation']
        self.assertEqual(observation['attack_attempts'], 2)
        self.assertEqual(observation['attack_rejections'], 0)
        self.assertEqual(observation['attack_errors'], 0)
        final = [f[1] for f in result['frames'] if f[0]=='skill_result'][0]
        self.assertIn('attack_rejections=0', final['detail'])
        self.assertIn('native kills required', final['detail'])

    def test_attack_exception_is_counted_not_reported_as_kill(self):
        result = self.run_native([{'ID':'poring','nameID':1002}], 'die "fixture attack error"')
        expected = {'samples':2, 'eligible_seen':2, 'attack_attempts':2, 'attack_errors':2, 'attack_rejections':0}
        self.assertEqual(result['during'].get('hunt_observation'), expected)
        final = [frame[1] for frame in result['frames'] if frame[0]=='skill_result'][0]
        self.assertIn('attack_attempts=2 attack_errors=2', final['detail'])
        self.assertIn('native kills required', final['detail'])


if __name__ == '__main__':
    unittest.main()
