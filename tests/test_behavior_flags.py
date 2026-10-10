import unittest
from packages.resident_behavior.flags import enrich
from packages.resident_behavior.config import load_behavior
from packages.resident_behavior.reducer import initial_state
from packages.resident_behavior.coordinator import stamp

class BehaviorFlagsTests(unittest.TestCase):
    def fixture(self):
        c=load_behavior('config/behavior.yaml');now=stamp('2026-10-10T14:00:00Z')
        s={'seq':1,'server_time':'2026-10-10T14:00:00Z','map':'prontera','x':156,'y':191,'hp':100,'hp_max':100,'weight':0,'weight_max':100,'zeny':0,'inventory':{},'base_level':20,'job_level':10,'alive':True,'near_monsters':[],'near_players':[],'current_action':None,'agreements':[]}
        m=initial_state(c,'mira',s,now)
        m['derived']['break_due']=0
        return c,now,s,m

    def test_fresh_peer_availability_and_target_relation(self):
        c,now,s,m=self.fixture()
        peer=dict(s, resident_id='borin')
        out=enrich(c,'mira',s,m,{'borin':{'snapshot':peer,'derived':{'sleeping':0},'schedule':{'sleep_at':now+3600}}},now)
        self.assertEqual(out['derived']['resident'],'borin')
        self.assertEqual(out['derived']['resident_valid'],1)
        self.assertEqual(out['derived']['meet_available'],1)
        self.assertEqual(out['relations']['borin']['trust'],25)
        peer['server_time']='2026-10-10T13:59:00Z'
        self.assertEqual(enrich(c,'mira',s,m,{'borin':{'snapshot':peer,'derived':{'sleeping':0},'schedule':{'sleep_at':now+3600}}},now)['derived']['meet_available'],0)

    def test_full_supplies_and_no_sellable_surplus_veto_empty_shop_loop(self):
        c,now,s,m=self.fixture()
        s.update(weight=86,weight_max=100,zeny=8105,inventory={'501':20,'512':256,'1202':28})
        out=enrich(c,'mira',s,m,{},now)
        self.assertEqual(out['derived']['service_need'],1)
        self.assertEqual(out['derived']['service_possible'],0)
        from packages.resident_behavior.engine import select
        ranking=select(c,'mira',s,out,now)
        self.assertNotEqual(ranking['primary']['action'],'town_service')
        s['inventory']['501']=0
        self.assertEqual(enrich(c,'mira',s,m,{},now)['derived']['service_possible'],1)

    def test_unknown_resources_stay_unknown_but_known_surplus_proves_service(self):
        c,now,s,m=self.fixture();s.update(hp=None,inventory=None,zeny=None)
        out=enrich(c,'mira',s,m,{},now)
        self.assertIsNone(out['derived']['service_possible'])
        self.assertIsNone(out['derived']['potions'])
        s['inventory']={'713':2}
        self.assertEqual(enrich(c,'mira',s,m,{},now)['derived']['service_possible'],1)

    def test_visits_habits_and_novelty_use_observed_position_purely(self):
        from copy import deepcopy
        c,now,s,m=self.fixture()
        c['residents']['mira']['habits'][0]['probability']=1
        before=deepcopy((c,s,m))
        out=enrich(c,'mira',s,m,{},now)
        self.assertEqual((c,s,m),before)
        self.assertEqual(out,enrich(c,'mira',s,m,{},now))
        self.assertEqual(out['visits']['square'],now)
        self.assertEqual(out['derived']['habit_due'],0)  # Already observed at ritual.
        self.assertNotEqual(out['derived']['novelty_place'],'square')
        unknown=dict(s,x=None)
        unknown_out=enrich(c,'mira',unknown,m,{},now)
        self.assertNotIn('square',unknown_out.get('visits',{}))
        self.assertIsNone(unknown_out['derived']['already_at_place'])

    def test_all_incoming_names_are_supported_and_expired_targets_cleared(self):
        c,now,s,m=self.fixture()
        for name in ('Tester','Vanya'):
            s['near_players']=[{'name':name,'x':157,'y':191}]
            m['heard']={'server_time':s['server_time'],'data':{'from':name,'channel':'whisper','text':'помоги'}}
            out=enrich(c,'mira',s,m,{},now)
            self.assertEqual(out['derived']['player'],name)
            self.assertEqual(out['derived']['help_request'],1)
            expired=enrich(c,'mira',s,out,{},now+61)
            self.assertIsNone(expired['derived']['player'])
            self.assertEqual(expired['derived']['player_valid'],0)

    def test_habit_is_not_rerolled_without_observed_hunt_block(self):
        c,now,s,m=self.fixture();c['residents']['mira']['habits'][0]['probability']=1
        s.update(x=100,y=100)
        first=enrich(c,'mira',s,m,{},now)
        seed=first['habit_state']['morning_square']['seed']
        later=dict(s,server_time=now+1000)
        out=enrich(c,'mira',later,first,{},now+1000)
        self.assertEqual(out['habit_state']['morning_square']['seed'],seed)
        self.assertEqual(out['rng_index'],first['rng_index'])
        later.update(x=156,y=191)
        arrived=enrich(c,'mira',later,out,{},now+1000)
        self.assertFalse(arrived['habit_state']['morning_square']['pending'])
        self.assertEqual(arrived['habit_state']['morning_square']['completed_at'],now+1000)

    def test_stale_position_does_not_validate_player_or_make_visit(self):
        c,now,s,m=self.fixture();s['server_time']=now-11
        s['near_players']=[{'name':'Vanya','x':156,'y':191}]
        m['heard']={'server_time':now,'data':{'from':'Vanya','channel':'whisper','text':'помоги'}}
        out=enrich(c,'mira',s,m,{},now)
        self.assertEqual(out['derived']['player_valid'],0)
        self.assertNotIn('square',out['visits'])

    def test_unknown_peer_health_and_near_sleep_are_unavailable(self):
        c,now,s,m=self.fixture()
        for changes,delay in [({'hp':None},3600),({},899)]:
            peer=dict(s,**changes)
            out=enrich(c,'mira',s,m,{'borin':{'snapshot':peer,'derived':{'sleeping':0},'schedule':{'sleep_at':now+delay}}},now)
            self.assertEqual(out['derived']['meet_available'],0)

    def test_recent_favorites_do_not_starve_novelty(self):
        c,now,s,m=self.fixture();c['residents']['mira']['habits']=[]
        m['visits']={'square':now-60,'east_view':now-120}
        out=enrich(c,'mira',s,m,{},now)
        self.assertEqual(out['derived']['favorite_place'],0)
        self.assertEqual(out['derived']['place'],out['derived']['novelty_place'])

    def test_own_echo_does_not_become_an_incoming_request(self):
        c,now,s,m=self.fixture()
        m['heard']={'server_time':now,'data':{'from':'Mira','channel':'whisper','text':'помоги'}}
        out=enrich(c,'mira',s,m,{},now)
        self.assertEqual(out['derived']['chat_due'],0)
        self.assertNotIn('Mira',out['relations'])

    def test_no_money_no_surplus_cannot_service_and_needs_no_heal_assumption(self):
        c=load_behavior('config/behavior.yaml');now=stamp('2026-10-10T14:00:00Z')
        s={'seq':1,'server_time':'2026-10-10T14:00:00Z','map':'prontera','x':156,'y':191,'hp':100,'hp_max':100,'weight':0,'weight_max':100,'zeny':0,'inventory':{},'base_level':20,'job_level':10,'alive':True,'near_monsters':[],'near_players':[],'current_action':None,'agreements':[]}
        m=initial_state(c,'mira',s,now);m=enrich(c,'mira',s,m,{},now)
        self.assertEqual(m['derived']['service_possible'],0)
        self.assertEqual(m['derived']['service_need'],0)
        self.assertEqual(m['derived']['help_request'],0)
        self.assertEqual(m['derived']['rest_safe'],1)

    def test_addressed_help_keeps_new_relationship_initial_not_heal_claim(self):
        c=load_behavior('config/behavior.yaml');now=stamp('2026-10-10T14:00:00Z')
        s={'seq':1,'server_time':'2026-10-10T14:00:00Z','map':'prontera','x':156,'y':191,'hp':100,'hp_max':100,'weight':0,'weight_max':100,'zeny':0,'inventory':{},'base_level':20,'job_level':10,'alive':True,'near_monsters':[],'near_players':[{'name':'Novi','x':157,'y':191}],'current_action':None,'agreements':[]}
        m=initial_state(c,'mira',s,now)
        m['heard']={'event_id':'heard-1','server_time':s['server_time'],'data':{'from':'Novi','channel':'public','text':'Mira, помоги, пойдем со мной'}}
        m=enrich(c,'mira',s,m,{},now)
        self.assertEqual(m['derived']['help_request'],1)
        self.assertEqual(m['derived']['player'],'Novi')
        self.assertEqual(m['relations']['Novi'],{'familiarity':0,'liking':0,'trust':25})
        self.assertNotIn('learned_skills',m['derived'])
