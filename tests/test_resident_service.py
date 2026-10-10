import pathlib,subprocess,tempfile,socket,json,time,unittest,sys
from unittest.mock import patch
from tools.events import query
ROOT=pathlib.Path(__file__).parents[1]
class ServiceTest(unittest.TestCase):
 def test_select_timeout_is_sanitized_read_failure(self):
  private='private-reader-path'
  with patch('tools.events.subprocess.run',side_effect=subprocess.TimeoutExpired(['mariadb',private],8,output='private output',stderr='private error')):
   with self.assertRaises(RuntimeError) as caught:query(private,'SELECT 1')
  self.assertEqual(str(caught.exception),'SELECT-only query timed out; private details withheld')
  self.assertTrue(caught.exception.__suppress_context__)
 def test_gateway_keeps_running_and_cursors_on_select_timeout(self):
  from packages.body_gateway.service import serve
  from packages.body_gateway.journal import Journal
  with tempfile.TemporaryDirectory() as d:
   store=d+'/world.sqlite';j=Journal(store)
   with j.db:j.db.executemany('INSERT INTO cursors VALUES(?,?)',[('witness',17),('chat',23),('picklog',31)])
   j.close()
   with patch('tools.events.subprocess.run',side_effect=subprocess.TimeoutExpired('reader',8)),patch('packages.body_gateway.service.signal.signal'):
    serve(d+'/body.sock',store,'resident_a',150001,'ResidentA',.15)
   j=Journal(store)
   try:
    self.assertEqual([j.cursor(s) for s in ('witness','chat','picklog')],[17,23,31])
    errors=j.db.execute("SELECT payload FROM operator_log WHERE op='witness_read_error'").fetchall()
    self.assertTrue(errors);self.assertEqual(json.loads(errors[0][0]),{'reason':'RuntimeError'})
   finally:j.close()
 def test_private_control_socket_returns_status_without_body(self):
  self.assertTrue((ROOT/'packages/body_gateway/service.py').exists(),'Bidirectional coordinator missing')
  with tempfile.TemporaryDirectory() as d:
   path=d+'/body.sock';p=subprocess.Popen([sys.executable,'-m','packages.body_gateway.service','--socket',path,'--store',d+'/world.sqlite','--duration','1','--no-witness'],cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   try:
    for _ in range(100):
     if pathlib.Path(path+'.control').exists():break
     if p.poll() is not None:self.fail(p.stderr.read().decode())
     time.sleep(.02)
    c=socket.socket(socket.AF_UNIX);c.connect(path+'.control');c.sendall(b'{"op":"status"}\n');data=b''
    while b'\n' not in data:data+=c.recv(4096)
    c.close();r=json.loads(data);self.assertTrue(r['ok']);self.assertIsNone(r['data']['telemetry']);cli=subprocess.run([sys.executable,'-m','packages.body_gateway.cli','--socket',path+'.control','status'],cwd=ROOT,capture_output=True,text=True);self.assertEqual(cli.returncode,0,cli.stderr);self.assertIsNone(json.loads(cli.stdout)['telemetry']);self.assertEqual(pathlib.Path(path+'.control').stat().st_mode&0o777,0o600);p.wait(timeout=3);self.assertEqual(p.returncode,0)
   finally:
    if p.poll() is None:p.kill();p.wait()
    p.stderr.close()
