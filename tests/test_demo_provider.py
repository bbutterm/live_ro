import pathlib,unittest
class ProviderTest(unittest.TestCase):
 def test_private_openrouter_config_overrides_only_resident_client(self):
  src=(pathlib.Path(__file__).parents[1]/'packages/resident_demo/main.py').read_text();self.assertIn("resident_llm.json",src);self.assertIn("key_file",src);self.assertIn("model=model",src);self.assertNotIn('sk-or-v1-',src)
