import pathlib,unittest
ROOT=pathlib.Path(__file__).parents[1]
class CWitnessCoverage(unittest.TestCase):
 def test_normal_respawn_and_city_paths_have_loadmap_observations(self):
  lines=(ROOT/'server/witness/maps.txt').read_text().splitlines()
  maps={l.split()[0]for l in lines if l and not l.startswith('//')}
  self.assertTrue({'izlude_a','prt_fild08a','prt_in','int_land01'}.issubset(maps))
