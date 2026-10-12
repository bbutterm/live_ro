import unittest
from packages.resident_world.supervisor import RestartPolicy, fallback, online

class WorldSupervisorTests(unittest.TestCase):
    def test_brain_execs_isolated_interpreter_without_restarting_bodies(self):
        from unittest.mock import patch
        from packages.resident_world import supervisor
        c={'brain_python':'/opt/test/python','residents':[{'resident_id':'resident_a'}]}
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp,patch.object(supervisor,'STATE',Path(tmp)),patch.object(supervisor,'config',return_value=c),patch('sys.argv',['world','--brain','resident_a']),patch('sys.executable','/usr/bin/python3'),patch.object(supervisor.os,'execv') as execute,patch.object(supervisor,'brain') as brain,patch.object(supervisor.os,'access',return_value=True):
            supervisor.main()
        execute.assert_called_once_with('/opt/test/python',['/opt/test/python','-m','packages.resident_world.supervisor','--brain','resident_a'])
        brain.assert_not_called()

    def test_missing_brain_interpreter_runs_the_brain_here_instead_of_crash_looping(self):
        from unittest.mock import patch
        from packages.resident_world import supervisor
        import sys,tempfile
        from pathlib import Path
        c={'brain_python':'/nonexistent/venv/bin/python','residents':[{'resident_id':'resident_a'}]}
        with tempfile.TemporaryDirectory() as tmp,patch.object(supervisor,'STATE',Path(tmp)),patch.object(supervisor,'config',return_value=c),patch('sys.argv',['world','--brain','resident_a']),patch.object(supervisor.os,'execv') as execute,patch.object(supervisor,'brain') as brain,self.assertLogs('world','WARNING'):
            supervisor.main()
        execute.assert_not_called();brain.assert_called_once_with({'resident_id':'resident_a'})

    def test_host_overlay_is_validated_and_a_broken_one_never_stops_the_world(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from packages.resident_world import supervisor
        with tempfile.TemporaryDirectory() as tmp,patch.object(supervisor,'STATE',Path(tmp)):
            base=supervisor.config()
            # The shared base in git is the new world: three residents, no host roles or operator personas.
            self.assertEqual([r['enabled'] for r in base['residents']],[True,True,True])
            self.assertFalse(any(k in r for r in base['residents'] for k in ('persona','support_target','duo_partner')))
            good={'residents':{'resident_a':{'enabled':False},'resident_b':{'support_target':'Borin','duo_partner':'Borin'}},'llm':{'max_hunt_minutes':10},'daily_llm_usd':3}
            (Path(tmp)/'world.local.json').write_text(json.dumps(good))
            c=supervisor.config()
            self.assertFalse(c['residents'][0]['enabled']);self.assertEqual(c['residents'][1]['support_target'],'Borin')
            self.assertEqual((c['llm']['max_hunt_minutes'],c['daily_llm_usd']),(10,3));self.assertFalse((Path(tmp)/'world.local.error').exists())
            for bad in ('{not json',json.dumps({'residents':{'nobody':{'enabled':False}}}),json.dumps({'residents':{'resident_a':{'profile':'/tmp/x'}}}),
                        json.dumps({'residents':{'resident_b':{'support_target':'Mira'}}}),json.dumps({'llm':{'max_hunt_minutes':True}}),json.dumps({'places':{}})):
                with self.subTest(bad=bad[:40]):
                    (Path(tmp)/'world.local.json').write_text(bad)
                    c=supervisor.config()  # last valid overlay, never a crash
                    self.assertFalse(c['residents'][0]['enabled']);self.assertTrue((Path(tmp)/'world.local.error').read_text())
            (Path(tmp)/'world.local.json').unlink()
            self.assertTrue(all(r['enabled'] for r in supervisor.config()['residents']))

    def test_status_names_the_running_code_and_a_stale_checkout(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from packages.resident_world import supervisor,cli
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'VERSION').write_text('v1-test\n')
            self.assertEqual(supervisor.code_version(tmp),'v1-test')  # a release dir without .git
            with patch.object(cli,'STATE',Path(tmp)),patch.object(supervisor,'code_version',return_value='abc1234'):
                (Path(tmp)/'identity.json').write_text(json.dumps({'version':'abc1234'}))
                self.assertEqual(cli.version_line(),'Код: abc1234')
                (Path(tmp)/'identity.json').write_text(json.dumps({'version':'0ld0000'}))
                self.assertIn('запущен 0ld0000, в checkout abc1234',cli.version_line())
                (Path(tmp)/'world.local.error').write_text('OVERLAY_FIELD:resident_a.profile')
                self.assertIn('ОШИБКА world.local.json (OVERLAY_FIELD',cli.version_line())

    def test_one_body_failure_does_not_change_other_restart_clocks(self):
        a,b=RestartPolicy(),RestartPolicy()
        self.assertEqual(a.failed(100),110)
        self.assertEqual(b.attempts,0)
        self.assertEqual(b.next_start,0)
        self.assertEqual(a.failed(110),130)
        for _ in range(10):a.failed(200)
        self.assertEqual(a.next_start,500)
    def test_long_healthy_period_resets_backoff(self):
        a=RestartPolicy();a.failed(100);a.started=110;a.healthy(411)
        self.assertEqual(a.attempts,0)
    def test_fallback_death_and_low_hp_never_hunt(self):
        self.assertEqual(fallback({'dead':True})['skill'],'respawn')
        self.assertEqual(fallback({'hp':10,'hp_max':100})['skill'],'rest')
        t={'hp':10,'hp_max':100,'weight':50,'weight_max':100,'map':'prt_fild08','inventory':{},'zeny':100}
        self.assertEqual(fallback(t)['params']['map'],'prontera')
        t['inventory']['909']=1
        self.assertEqual(fallback(t)['params']['map'],'prt_in')
        t.update(map='prt_in',hp=70)
        self.assertEqual(fallback(t)['skill'],'sell_loot')
    def test_fallback_reuses_life_supply_rules(self):
        t={'hp':100,'hp_max':100,'inventory':{},'map':'prontera','zeny':100}
        self.assertEqual(fallback(t)['skill'],'travel_to')
        t.update(map='prt_in',inventory={'501':20})
        self.assertEqual(fallback(t)['params']['map'],'prt_fild08')
    def test_poor_resident_earns_zeny_instead_of_being_stuck_at_shop(self):
        t={'hp':100,'hp_max':100,'inventory':{},'map':'prt_in','zeny':0}
        self.assertEqual(fallback(t)['skill'],'travel_to')
        self.assertEqual(fallback(t)['params']['map'],'prt_fild08')
        t['map']='prt_fild08'
        self.assertEqual(fallback(t)['skill'],'hunt')
    def test_status_requires_fresh_body_and_witness(self):
        s={'age':1,'projection':{'server_online':{'value':True,'ts':100},'server_pos':{'ts':100}}}
        self.assertTrue(online(s,102))
        self.assertFalse(online(s,120))
        s['age']=10;self.assertFalse(online(s,102))
    def test_owner_status_uses_russian_activity_and_reason(self):
        from packages.resident_world.cli import describe
        self.assertEqual(describe('hunt','HUNT_INTERVAL'),('охота','Охочусь на поле'))
        self.assertEqual(describe('rest','Восстанавливаю здоровье'),('отдых','Восстанавливаю здоровье'))
    def test_persistent_gateway_is_explicit_not_default(self):
        from pathlib import Path
        text=Path('packages/body_gateway/service.py').read_text()
        self.assertIn('--persistent',text)
        self.assertIn('duration is None',text)
