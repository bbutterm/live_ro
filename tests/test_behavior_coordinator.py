import unittest
from packages.resident_behavior.coordinator import coordinate, stamp

class BehaviorCoordinatorTests(unittest.TestCase):
    def fixture(self):
        from packages.resident_behavior.config import load_behavior
        c=load_behavior('config/behavior.yaml');now=stamp('2026-10-10T14:00:00Z')
        s={'seq':1,'server_time':'2026-10-10T14:00:00Z','map':'prontera','x':156,'y':191,'hp':100,'hp_max':100,'weight':0,'weight_max':100,'inventory':{'501':3},'alive':True,'near_monsters':[],'current_action':None,'agreements':[]}
        r={'primary':{'action':'hunt','args':{'map':'prt_fild08','minutes':10}},'candidates':[{},{}]}
        return c,now,s,r

    def test_sleep_goes_home_then_idles_without_llm(self):
        c,now,s,r=self.fixture();m={'derived':{'sleeping':1}}
        out=coordinate(c,'mira',s,m,r,now)
        self.assertEqual((out['action'],out['args']),('walk_to',{'place':'home_mira'}))
        self.assertFalse(out['llm_allowed'])
        s.update(x=225,y=155)
        out=coordinate(c,'mira',s,m,r,now)
        self.assertEqual(out['action'],'idle')
        self.assertGreaterEqual(out['args']['minutes'],8)

    def test_sleep_and_break_do_not_break_minimum_but_completed_motor_does(self):
        c,now,s,r=self.fixture()
        s['current_action']={'action_id':'walking','action':'walk_to','args':{'place':'square'},'started_at':now-30,'result':None}
        m={'derived':{'sleeping':1,'break_due':1}}
        self.assertEqual(coordinate(c,'mira',s,m,r,now)['action_id'],'walking')
        s['current_action']['result']='done'
        self.assertEqual(coordinate(c,'mira',s,m,r,now)['args'],{'place':'home_mira'})

    def test_critical_hp_danger_precedes_meet_without_active_hunt(self):
        c,now,s,r=self.fixture();s.update(hp=24,near_monsters=[{'id':1002}])
        s['agreements']=[{'status':'accepted','participants':['mira','borin'],'place':'square','accepted_at':now-60,'deadline':now+540}]
        self.assertEqual(coordinate(c,'mira',s,{'derived':{'sleeping':0}},r,now)['action'],'town_service')

    def test_streak_and_no_potions_veto_continuing_hunt(self):
        c,now,s,r=self.fixture()
        s['current_action']={'action_id':'hunting','action':'hunt','args':r['primary']['args'],'started_at':now-30,'result':None}
        for hp,inventory,derived in [(100,{'501':3},{'hunt_streak':90}),(80,{},{}),(80,None,{})]:
            with self.subTest(hp=hp,inventory=inventory,derived=derived):
                s.update(hp=hp,inventory=inventory)
                out=coordinate(c,'mira',s,{'derived':derived},r,now)
                self.assertNotEqual(out['action'],'hunt')
                self.assertEqual(out['interrupt'],'hunting')

    def test_invalid_accepted_meet_does_not_override_motor(self):
        c,now,s,r=self.fixture()
        s['agreements']=[{'status':'accepted','participants':['mira','mira'],'place':'absent','accepted_at':now-60,'deadline':now+540}]
        out=coordinate(c,'mira',s,{'derived':{'sleeping':0}},r,now)
        self.assertEqual(out['action'],'hunt')

    def test_fractional_break_schedule_emits_valid_integer_idle_duration(self):
        from packages.resident_behavior.contracts import validate_action
        c,now,s,r=self.fixture();s.update(x=225,y=155)
        m={'derived':{'break_due':1},'schedule':{'break_minutes':6.4}}
        out=coordinate(c,'mira',s,m,r,now)
        validate_action(out['action'],out['args'],'mira')
        self.assertEqual(out['args'],{'minutes':7})

    def test_break_home_motor_and_two_no_path_fallback_are_pure(self):
        from copy import deepcopy
        c,now,s,r=self.fixture();m={'derived':{'break_due':1},'schedule':{'break_minutes':7}}
        original=deepcopy((c,s,m,r))
        out=coordinate(c,'mira',s,m,r,now)
        self.assertEqual(out['args'],{'place':'home_mira'})
        self.assertEqual((c,s,m,r),original)
        self.assertEqual(out,coordinate(c,'mira',s,m,r,now))
        s.update(x=225,y=155)
        self.assertEqual(coordinate(c,'mira',s,m,r,now)['args'],{'minutes':7})
        m['consecutive_no_path']=2
        self.assertEqual(coordinate(c,'mira',s,m,r,now)['args'],{'minutes':1})

    def test_accepted_meet_overrides_break_and_preserves_meet_timer(self):
        c,now,s,r=self.fixture();m={'derived':{'break_due':1,'sleeping':0,'meet_available':1}}
        s['agreements']=[{'meeting_id':'ok','status':'accepted','participants':['mira','borin'],'place':'square','accepted_at':now-60,'deadline':now+540}]
        args={'resident':'borin','place':'square'}
        s['current_action']={'action_id':'meeting','action':'meet','args':args,'started_at':now-30,'result':None}
        out=coordinate(c,'mira',s,m,r,now)
        self.assertEqual(out['action_id'],'meeting')
        self.assertEqual(out['started_at'],now-30)
        self.assertFalse(out['llm_allowed'])
        m['derived']['meet_available']=0
        self.assertNotEqual(coordinate(c,'mira',dict(s,current_action=None),m,r,now)['action'],'meet')

    def test_meet_becomes_unavailable_before_minimum(self):
        c,now,s,r=self.fixture()
        s['current_action']={'action_id':'meeting','action':'meet','args':{'resident':'borin','place':'square'},'started_at':now-30,'result':None}
        out=coordinate(c,'mira',s,{'derived':{'meet_available':0}},r,now)
        self.assertEqual(out['action'],'idle')
        self.assertEqual(out['interrupt'],'meeting')
        self.assertEqual(out['source'],'safety')

    def test_stale_state_only_idles_and_cancels_existing_motor(self):
        snapshot={'resident_id':'mira','seq':3,'server_time':'2026-10-10T10:00:00Z','alive':True,'current_action':{'action_id':'running','action':'hunt','args':{'minutes':10},'started_at':'2026-10-10T09:59:00Z','result':None}}
        result=coordinate({'actions':{}},'mira',snapshot,{}, {'primary':{'action':'hunt','args':{'minutes':10}}},1791626411)
        self.assertEqual(result['action'],'idle')
        self.assertEqual(result['args'],{'minutes':1})
        self.assertEqual(result['source'],'safety')
        self.assertEqual(result['interrupt'],'running')
        self.assertEqual(result['trigger'],'stale_state')

    def test_minimum_duration_preserves_action_identity_and_timer(self):
        now=stamp('2026-10-10T10:00:00Z')
        active={'action_id':'same-hunt','action':'hunt','args':{'map':'prt_fild08','minutes':10},'started_at':'2026-10-10T09:59:00Z','result':None}
        snapshot={'resident_id':'mira','seq':4,'server_time':'2026-10-10T10:00:00Z','alive':True,'hp':100,'hp_max':100,'weight':0,'weight_max':100,'inventory':{'501':3},'near_monsters':[],'current_action':active}
        result=coordinate({'actions':{'hunt':{'min_minutes':5}}},'mira',snapshot,{'derived':{}},{'primary':{'action':'idle','args':{'minutes':5}},'candidates':[{},{}]},now,trigger='actionable_chat')
        self.assertEqual(result['action_id'],'same-hunt')
        self.assertEqual(result['action'],'hunt')
        self.assertTrue(result['continuation'])
        self.assertFalse(result['llm_allowed'])
        self.assertEqual(result['started_at'],active['started_at'])

    def test_hunt_danger_overrides_minimum_duration(self):
        now=stamp('2026-10-10T10:00:00Z')
        active={'action_id':'hunt-low-hp','action':'hunt','args':{'map':'prt_fild08','minutes':10},'started_at':'2026-10-10T09:59:00Z','result':None}
        snapshot={'seq':6,'server_time':'2026-10-10T10:00:00Z','alive':True,'hp':24,'hp_max':100,'weight':10,'weight_max':100,'near_monsters':[{'id':1002,'x':1,'y':1}],'current_action':active}
        result=coordinate({'actions':{'hunt':{'min_minutes':5}}},'mira',snapshot,{'drives':{'fear':10},'derived':{}},{'primary':{'action':'hunt','args':active['args']}},now)
        self.assertEqual(result['action'],'town_service')
        self.assertEqual(result['args'],{})
        self.assertEqual(result['source'],'safety')
        self.assertEqual(result['interrupt'],'hunt-low-hp')
        self.assertFalse(result['continuation'])

    def test_accepted_meeting_has_priority_over_inertia(self):
        now=stamp('2026-10-10T10:00:00Z')
        active={'action_id':'old-hunt','action':'hunt','args':{'map':'prt_fild08','minutes':10},'started_at':'2026-10-10T09:59:00Z','result':None}
        snapshot={'seq':7,'server_time':'2026-10-10T10:00:00Z','alive':True,'hp':100,'hp_max':100,'weight':0,'weight_max':100,'inventory':{'501':3},'near_monsters':[],'current_action':active,'agreements':[{'meeting_id':'agreed','participants':['mira','borin'],'place':'square','accepted_at':'2026-10-10T09:59:00Z','deadline':'2026-10-10T10:09:00Z','status':'accepted'}]}
        result=coordinate({'actions':{'hunt':{'min_minutes':5}}},'mira',snapshot,{'derived':{'sleeping':0}},{'primary':{'action':'hunt','args':active['args']}},now)
        self.assertEqual(result['action'],'meet')
        self.assertEqual(result['args'],{'resident':'borin','place':'square'})
        self.assertEqual(result['interrupt'],'old-hunt')
        self.assertEqual(result['trigger'],'accepted_meet')
