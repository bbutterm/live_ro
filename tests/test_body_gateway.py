import json,os,pathlib,socket,sqlite3,subprocess,sys,tempfile,time,unittest
ROOT=pathlib.Path(__file__).parents[1]
class GatewayTest(unittest.TestCase):
 def test_failed_bind_does_not_delete_another_owners_path(self):
  from packages.body_gateway import gateway
  from unittest.mock import patch
  original=socket.socket
  class RacedSocket(original):
   def bind(self,address):
    pathlib.Path(address).write_text('other owner')
    raise OSError('simulated bind race')
  with tempfile.TemporaryDirectory() as d:
   path=pathlib.Path(d)/'body.sock'
   with patch.object(gateway.socket,'socket',RacedSocket):
    with self.assertRaises(OSError):gateway.serve(path,str(pathlib.Path(d)/'db.sqlite'),'resident_a',150001,1)
   self.assertTrue(path.exists(),'Failed bind deleted an unowned endpoint')
   self.assertEqual(path.read_text(),'other owner')
 def test_unix_peer_is_journalled_under_epoch_seq(self):
  self.assertTrue((ROOT/'packages/body_gateway/gateway.py').exists(),'Unix gateway missing')
  with tempfile.TemporaryDirectory() as d:
   sock=pathlib.Path(d)/'body.sock';db=pathlib.Path(d)/'journal.sqlite'
   p=subprocess.Popen([sys.executable,'-m','packages.body_gateway.gateway','--socket',str(sock),'--store',str(db),'--resident','resident_a','--char-id','150001','--duration','1'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   try:
    end=time.monotonic()+2
    while not sock.exists() and p.poll() is None and time.monotonic()<end:time.sleep(.02)
    self.assertTrue(sock.exists());self.assertEqual(sock.stat().st_mode&0o777,0o600)
    c=socket.socket(socket.AF_UNIX);c.connect(str(sock))
    hello={'proto':1,'type':'hello','seq':1,'ts':time.time(),'resident':'resident_a','char_id':150001,'body_epoch':'cf844d0a-3d79-4b52-a327-e4ff0adf5090'}
    c.sendall((json.dumps(hello)+'\n').encode());c.close()
    out,err=p.communicate(timeout=3);self.assertEqual(p.returncode,0,err)
    saved=sqlite3.connect(db).execute('SELECT source_key,payload FROM events').fetchone()
    self.assertEqual(saved[0],hello['body_epoch']+':1');self.assertEqual(json.loads(saved[1]),hello)
    self.assertFalse(sock.exists())
   finally:
    if p.poll() is None:p.terminate();p.wait(timeout=2)
