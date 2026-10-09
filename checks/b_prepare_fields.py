"""Build private OpenKore fields from explicitly ordered native rAthena caches.
Run: python3 checks/b_prepare_fields.py --cache db/map_cache.dat --cache db/re/map_cache.dat --out /private/run/fields --fallback /path/openkore/fields
Last cache wins, as with rAthena renewal/import overrides. Does not edit upstream.
"""
import argparse,gzip,hashlib,json,pathlib,sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from packages.body_gateway.fields import read_cache,encode_field

def main():
 p=argparse.ArgumentParser();p.add_argument('--cache',action='append',required=True);p.add_argument('--out',required=True);p.add_argument('--fallback');a=p.parse_args()
 out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True,mode=0o700);maps={};hashes={}
 for source in a.cache:
  data=pathlib.Path(source).read_bytes();maps.update(read_cache(data));hashes[source]=hashlib.sha256(data).hexdigest()
 for name,v in maps.items():
  target=out/(name+'.fld2.gz')
  if target.is_symlink():target.unlink()
  target.write_bytes(gzip.compress(encode_field(*v),compresslevel=1,mtime=0))
  # Never reuse distance/weight caches built against another geometry.
  for suffix in ('.dist','.weight'):
   cache=out/(name+suffix)
   if cache.exists() or cache.is_symlink():cache.unlink()
 if a.fallback:
  for source in pathlib.Path(a.fallback).glob('*.fld2*'):
   target=out/source.name
   if not target.exists():target.symlink_to(source.resolve())
 (out/'provenance.json').write_text(json.dumps(dict(sources=hashes,maps=len(maps)),indent=2))
 print(json.dumps(dict(maps=len(maps),out=str(out))))
if __name__=='__main__':main()
