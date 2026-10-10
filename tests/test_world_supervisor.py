import unittest
from packages.resident_world.supervisor import RestartPolicy, fallback, online

class WorldSupervisorTests(unittest.TestCase):
    def test_brain_execs_isolated_interpreter_without_restarting_bodies(self):
        from unittest.mock import patch
        from packages.resident_world import supervisor
        c={'brain_python':'/opt/test/python','residents':[{'resident_id':'resident_a'}]}
        with patch.object(supervisor,'config',return_value=c),patch('sys.argv',['world','--brain','resident_a']),patch('sys.executable','/usr/bin/python3'),patch.object(supervisor.os,'execv') as execute,patch.object(supervisor,'brain') as brain:
            supervisor.main()
        execute.assert_called_once_with('/opt/test/python',['/opt/test/python','-m','packages.resident_world.supervisor','--brain','resident_a'])
        brain.assert_not_called()

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
