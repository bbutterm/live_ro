"""Durable action journal. Only the executor can assert server-confirmed outcomes."""
import sqlite3,json,time,uuid
TERMINAL={'confirmed','failed','timed_out','cancelled','rejected_local','escalated'}
ALLOWED={'planned':{'dispatched','rejected_local'},'dispatched':{'accepted','verifying','failed','unknown','timed_out','cancelled'},'accepted':{'running','verifying','failed','unknown','timed_out','cancelled'},'running':{'verifying','failed','unknown','timed_out','cancelled'},'verifying':{'confirmed','failed','unknown','timed_out','cancelled'},'unknown':{'verifying','confirmed','failed','escalated','cancelled'}}
class Journal:
 def __init__(self,path):
  self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA journal_mode=WAL')
  self.db.executescript('''CREATE TABLE IF NOT EXISTS events(source TEXT,source_key TEXT,ts REAL,payload TEXT,PRIMARY KEY(source,source_key));
CREATE TABLE IF NOT EXISTS actions(action_id TEXT PRIMARY KEY,resident TEXT,skill TEXT,params TEXT,idem_key TEXT,deadline REAL,epoch TEXT,baseline INTEGER,state TEXT,code TEXT,created REAL,updated REAL,UNIQUE(resident,idem_key));
CREATE TABLE IF NOT EXISTS action_transitions(id INTEGER PRIMARY KEY,action_id TEXT,ts REAL,state TEXT,code TEXT,evidence TEXT);
CREATE TABLE IF NOT EXISTS operator_log(id INTEGER PRIMARY KEY,ts REAL,op TEXT,payload TEXT);
CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY,value INTEGER);
CREATE INDEX IF NOT EXISTS events_source_ts ON events(source,ts);
CREATE INDEX IF NOT EXISTS actions_resident_state ON actions(resident,state);
CREATE INDEX IF NOT EXISTS transitions_action ON action_transitions(action_id);
CREATE INDEX IF NOT EXISTS operator_log_ts ON operator_log(ts);''')
 def plan(self,resident,skill,params,idem,deadline,epoch,baseline):
  row=self.db.execute('SELECT * FROM actions WHERE resident=? AND idem_key=?',(resident,idem)).fetchone()
  if row:return dict(row)
  aid=str(uuid.uuid4());now=time.time()
  with self.db:
   self.db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(aid,resident,skill,json.dumps(params),idem,deadline,epoch,baseline,'planned','',now,now))
   self.db.execute('INSERT INTO action_transitions(action_id,ts,state,code,evidence) VALUES(?,?,?,?,?)',(aid,now,'planned','','{}'))
  return self.action(aid)
 def action(self,aid):
  row=self.db.execute('SELECT * FROM actions WHERE action_id=?',(aid,)).fetchone()
  if not row:raise ValueError('UNKNOWN_ACTION')
  return dict(row)
 def actions(self):return [dict(r) for r in self.db.execute('SELECT * FROM actions ORDER BY created')]
 def open_actions(self,resident):
  """Non-terminal actions only (indexed): the hot path must not rescan the whole history."""
  marks=','.join('?'*len(TERMINAL))
  return [dict(r) for r in self.db.execute(f'SELECT * FROM actions WHERE resident=? AND state NOT IN ({marks}) ORDER BY created',(resident,*sorted(TERMINAL)))]
 def purge(self,now=None,keep_events=48*3600,keep_actions=30*86400):
  """Bounded growth for a permanent world: old server/body rows, operator log and long
  finished actions go; anything an open action may still need as evidence stays."""
  now=time.time() if now is None else now
  oldest_open=self.db.execute(f"SELECT MIN(created) FROM actions WHERE state NOT IN ({','.join('?'*len(TERMINAL))})",tuple(sorted(TERMINAL))).fetchone()[0]
  cut=min(now-keep_events,(oldest_open or now)-60)
  with self.db:
   n=self.db.execute('DELETE FROM events WHERE ts<?',(cut,)).rowcount
   n+=self.db.execute('DELETE FROM operator_log WHERE ts<?',(now-keep_events,)).rowcount
   old=f"SELECT action_id FROM actions WHERE updated<? AND state IN ({','.join('?'*len(TERMINAL))})"
   n+=self.db.execute(f'DELETE FROM action_transitions WHERE action_id IN ({old})',(now-keep_actions,*sorted(TERMINAL))).rowcount
   n+=self.db.execute(f"DELETE FROM actions WHERE updated<? AND state IN ({','.join('?'*len(TERMINAL))})",(now-keep_actions,*sorted(TERMINAL))).rowcount
  self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
  return n
 def transition(self,aid,state,code='',evidence=None):
  a=self.action(aid)
  if a['state']==state:return
  if state not in ALLOWED.get(a['state'],set()):raise ValueError(f"Invalid transition {a['state']}->{state}")
  now=time.time()
  with self.db:
   self.db.execute('UPDATE actions SET state=?,code=?,updated=? WHERE action_id=?',(state,code,now,aid))
   self.db.execute('INSERT INTO action_transitions(action_id,ts,state,code,evidence) VALUES(?,?,?,?,?)',(aid,now,state,code,json.dumps(evidence or {})))
 def trace(self,aid):return [dict(r) for r in self.db.execute('SELECT * FROM action_transitions WHERE action_id=? ORDER BY id',(aid,))]
 def event(self,source,key,ts,payload,cursor=None):
  with self.db:
   self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)',(source,str(key),ts,json.dumps(payload)))
   if cursor is not None:self.db.execute('INSERT OR REPLACE INTO cursors VALUES(?,?)',(source,cursor))
 def cursor(self,source):
  r=self.db.execute('SELECT value FROM cursors WHERE source=?',(source,)).fetchone();return r[0] if r else 0
 def log(self,op,payload):
  with self.db:self.db.execute('INSERT INTO operator_log(ts,op,payload) VALUES(?,?,?)',(time.time(),op,json.dumps(payload)))
 def close(self):self.db.close()
