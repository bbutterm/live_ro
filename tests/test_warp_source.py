import pathlib,tempfile,unittest
class WarpSourceTest(unittest.TestCase):
 def test_legacy_non_utf8_npc_comment_does_not_hide_ascii_warp(self):
  from packages.body_gateway.portals import read_warps
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);(root/'warp.txt').write_bytes(b'// legacy \xa1\xa2\nprontera,156,22,0\twarp\tprt001\t3,2,prt_fild08,170,375\n')
   self.assertEqual(read_warps(root,['warp.txt']),['prontera 156 22 prt_fild08 170 375'])

 def test_common_and_renewal_manifests_are_both_followed(self):
  from packages.body_gateway.portals import read_warps
  with tempfile.TemporaryDirectory() as d:
   root=pathlib.Path(d);(root/'npc/re').mkdir(parents=True)
   (root/'npc/scripts_warps.conf').write_text('npc: npc/prontera.txt\n//npc: npc/disabled.txt\n')
   (root/'npc/re/scripts_warps.conf').write_text('npc: npc/re/field.txt\n')
   (root/'npc/prontera.txt').write_text('prontera,156,22,0\twarp\tprt001\t3,2,prt_fild08,170,375\n')
   (root/'npc/re/field.txt').write_text('prt_fild08,170,378,0\twarp\tprt002\t2,2,prontera,156,26\n')
   rows=read_warps(root,['npc/scripts_warps.conf','npc/re/scripts_warps.conf'])
   self.assertIn('prontera 156 22 prt_fild08 170 375',rows)
   self.assertIn('prt_fild08 170 378 prontera 156 26',rows)
