import importlib.util,unittest

class LLMDecisionTest(unittest.TestCase):
    def test_feedback_excludes_speculation_and_blocks_same_failed_hunt(self):
        from packages.resident_demo.llm_decision import decision_context
        t=dict(map='prt_fild08',pos={'x':170,'y':240},dead=False,hp=100,hp_max=100,zeny=100,inventory={'501':20})
        previous=[{'decision':{'goal':'hunt','reason':'выдуманные 5 синих зелий'},'provider_usage':{'cost':1},'outcomes':[{'skill':'hunt','state':'failed','code':'NO_TARGET'}]}]
        c=decision_context(t,previous)
        self.assertNotIn('hunt',c['available_goals'])
        self.assertIn('return_town',c['available_goals'])
        self.assertNotIn('выдуманные',str(c))
        self.assertNotIn('provider_usage',str(c))

    def test_context_names_consumables_without_inventing_stock(self):
        import packages.resident_demo.llm_decision as module
        self.assertTrue(hasattr(module,'decision_context'))
        state=dict(map='prontera',pos={'x':150,'y':150},dead=False,hp=100,hp_max=100,zeny=100,inventory={'501':20})
        context=module.decision_context(state,[])
        self.assertEqual(context['self']['resources'],{'red_potions':20,'blue_potions':0})
        self.assertNotIn('inventory',context['self'])
        self.assertEqual(context['self']['items'],[{'item_id':'501','count':20}])
        self.assertEqual(state['inventory'],{'501':20})
        self.assertFalse(context['requirements']['hunt']['blue_potions_required'])
        self.assertEqual(context['requirements']['hunt']['min_red_potions'],3)

    def test_model_selects_only_feasible_catalogue_plan(self):
        self.assertIsNotNone(importlib.util.find_spec('packages.resident_demo.llm_decision'))
        from packages.resident_demo.llm_decision import options,plan
        state=dict(map='prontera',pos={'x':150,'y':150},dead=False,hp=100,hp_max=100,zeny=100,inventory={'501':20})
        self.assertEqual(set(options(state)),{'hunt','wait'})
        steps=plan({'goal':'hunt','reason':'Нужен заработок'},state)
        self.assertEqual([s['skill'] for s in steps],['travel_to','hunt'])
        self.assertEqual(steps[-1]['params']['min_kills'],1)
        for decision in ({'goal':'buy_supplies','reason':'x'},{'goal':'shell','reason':'x'},
                         {'goal':'hunt','reason':'x','command':'@item 501 100'}):
            with self.assertRaises(ValueError):plan(decision,state)
        low={**state,'hp':50,'inventory':{'501':1}}
        self.assertNotIn('hunt',options(low))
        self.assertIn('rest',options(low));self.assertIn('buy_supplies',options(low))
        self.assertLessEqual(plan({'goal':'buy_supplies','reason':'Мало зелий'},low)[-1]['params']['max_zeny'],300)
