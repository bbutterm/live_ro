"""Read pinned rAthena native mapcache; encode OpenKore field flags without inventing geometry.
Cache headers have native trailing alignment: main 8 bytes, map 20 bytes.
GAT semantics: src/map/map.cpp map_gat2cell; fld2: OpenKore Field.pm.
"""
import struct,zlib

def read_cache(data):
 size,count=struct.unpack_from('<IH',data);offset=8;out={}
 if size!=len(data):raise ValueError('cache size mismatch')
 for _ in range(count):
  name,w,h,n=struct.unpack_from('<12shhI',data,offset);offset+=20
  if w<=0 or h<=0 or offset+n>len(data):raise ValueError('invalid map entry')
  cells=zlib.decompress(data[offset:offset+n]);offset+=n
  if len(cells)!=w*h:raise ValueError('cell count mismatch')
  out[name.split(b'\0')[0].decode('ascii')]=(w,h,cells)
 if offset!=len(data):raise ValueError('trailing cache bytes')
 return out

def encode_field(w,h,cells):
 if len(cells)!=w*h:raise ValueError('cell count mismatch')
 flags=(3,0,3,7,3,2,3)
 if cells and max(cells)>6:raise ValueError('unknown GAT type')
 return struct.pack('<HH',w,h)+cells.translate(bytes.maketrans(bytes(range(7)),bytes(flags)))
