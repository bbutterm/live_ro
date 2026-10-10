import unittest
from packages.resident_behavior.cutover import child_matches

class CutoverTests(unittest.TestCase):
    def test_only_exact_actor_child_is_replaceable(self):
        r={'resident_id':'resident_a','profile':'/profiles/a'}
        self.assertTrue(child_matches(['python','-m','packages.resident_world.supervisor','--brain','resident_a'],'brain',r))
        self.assertFalse(child_matches(['python','-m','packages.resident_world.supervisor'],'brain',r))
        self.assertFalse(child_matches(['python','-m','packages.resident_world.supervisor','--brain','resident_b'],'brain',r))
        self.assertTrue(child_matches(['perl','openkore.pl','--control=/profiles/a/control'],'body',r))
        self.assertFalse(child_matches(['perl','openkore.pl','--control=/profiles/b/control'],'body',r))
        self.assertTrue(child_matches(['python','-m','packages.body_gateway.service','--resident','resident_a'],'gateway',r))
        self.assertFalse(child_matches(['python','-m','packages.body_gateway.service','--resident','resident_b'],'gateway',r))
