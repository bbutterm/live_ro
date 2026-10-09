import pathlib,subprocess,tempfile,socket,json,time,unittest,sys
ROOT=pathlib.Path(__file__).parents[1]
class ServiceTest(unittest.TestCase):
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
