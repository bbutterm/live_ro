import pathlib,re,unittest
ROOT=pathlib.Path(__file__).parents[1]
class WitnessContract(unittest.TestCase):
 def test_insert_columns_exist_in_installed_schema_contract(self):
  schema=(ROOT/'server/schema/001_witness.sql').read_text()
  table=re.search(r'CREATE TABLE residents\.resident_events\s*\((.*?)\);',schema,re.S)
  assert table is not None
  columns=set(re.findall(r'\b(\w+)\s+(?:INT|BIGINT|DATETIME|VARCHAR|SMALLINT)\b',table.group(1)))
  npc=(ROOT/'server/witness/residents_witness.txt').read_text()
  inserts=re.findall(r'INSERT INTO residents\.resident_events\(([^)]+)\)',npc)
  self.assertTrue(inserts)
  for insert in inserts:self.assertFalse(set(insert.split(','))-columns,'Witness INSERT uses nonexistent schema columns')
