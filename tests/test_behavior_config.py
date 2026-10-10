import unittest


class ConfigTests(unittest.TestCase):
    def test_resident_goals_and_habits_require_lists(self):
        import copy
        from packages.resident_behavior.config import load_behavior, validate_behavior
        config = load_behavior('config/behavior.yaml')
        for field in ('goals', 'habits'):
            for value in ({}, '', None, (), 1, True):
                bad = copy.deepcopy(config)
                bad['residents']['mira'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_behavior(bad)
            good = copy.deepcopy(config)
            good['residents']['mira'][field] = []
            self.assertEqual(validate_behavior(good), good)

    def test_load_and_strict_rejections(self):
        import copy
        import tempfile
        from pathlib import Path
        import yaml
        from packages.resident_behavior.config import load_behavior, config_hash
        config = load_behavior('config/behavior.yaml')
        self.assertEqual(len(config_hash(config)), 64)
        self.assertEqual(config_hash(config), config_hash(dict(reversed(list(config.items())))))
        mutations = [
            lambda c: c.update(extra=1),
            lambda c: c['actions']['hunt'].update(utility='unknown+1'),
            lambda c: c['actions']['hunt'].update(utility='hp.real'),
            lambda c: c['residents']['mira']['traits'].update(greed=2),
            lambda c: c['residents']['mira'].update(home_place='missing'),
            lambda c: c['actions']['meet']['default_args'].update(resident='missing'),
            lambda c: c['speech']['defaults'].update(typo_rate=-1),
            lambda c: c['places']['square'].update(x=-1),
            lambda c: c['drives']['tired']['on_event'].update(mystery=1),
            lambda c: c['residents']['mira']['habits'][0].update(extra=1),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'behavior.yaml'
            for mutation in mutations:
                bad = copy.deepcopy(config); mutation(bad)
                path.write_text(yaml.safe_dump(bad), encoding='utf-8')
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): load_behavior(path)
            path.write_text('version: 1\nversion: 1\n')
            with self.assertRaises(ValueError): load_behavior(path)


class ExpressionTests(unittest.TestCase):
    def test_safe_ast_and_equality(self):
        from packages.resident_behavior.expressions import evaluate, identifiers
        self.assertEqual(evaluate('clamp(2+x*3,0,10)', {'x': 4}), 10)
        self.assertTrue(evaluate('x=0 or y>=2', {'x': 0, 'y': 3}, predicate=True))
        self.assertEqual(identifiers('max(x,y)-2'), {'x', 'y'})
        for source in ['x.real', '__import__("os")', 'x**2', 'inventory[501]', 'True', 'min(x)', 'lambda: 1']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                identifiers(source)
        with self.assertRaises(ValueError):
            evaluate('x+1', {'x': None})
        with self.assertRaises(ValueError):
            evaluate('1/0', {})
