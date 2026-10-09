import struct,zlib,unittest
from packages.body_gateway.fields import read_cache,encode_field
class Fields(unittest.TestCase):
 def test_padded_native_cache_and_walkability(self):
  raw=bytes([0,1,2,3,4,5,6]);z=zlib.compress(raw)
  b=struct.pack('<IH2x',28+len(z),1)+struct.pack('<12shhI',b'izlude',7,1,len(z))+z
  maps=read_cache(b);self.assertEqual(maps['izlude'],(7,1,raw))
  self.assertEqual(encode_field(*maps['izlude']),struct.pack('<HH',7,1)+bytes([3,0,3,7,3,2,3]))
 def test_bad_cell_count_rejected(self):
  z=zlib.compress(b'\0');b=struct.pack('<IH2x',28+len(z),1)+struct.pack('<12shhI',b'izlude',2,1,len(z))+z
  with self.assertRaises(ValueError):read_cache(b)
