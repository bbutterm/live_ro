import pathlib,unittest
class StationaryProbe(unittest.TestCase):
 def test_timer_does_not_suppress_stationary_position_probes(self):
  source=(pathlib.Path(__file__).parents[1]/'server/witness/residents_witness.txt').read_text()
  self.assertIn('OnTimer5000:',source)
  self.assertNotIn('.@dx*.@dx + .@dy*.@dy <= 9) return;',source,'ESTOP requires fresh stationary server probes, not absence of movement events')
