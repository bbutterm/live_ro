#!/usr/bin/env python3
"""Witness reader v0: SELECT-only, raw DB timestamps, bounded observation."""
import argparse,json,subprocess,time,sqlite3,os

class EventStore:
    """Atomic durable inbox and cursor, partitioned by explicit source generation."""
    def __init__(self,path):
        self.db=sqlite3.connect(path,timeout=5)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS events(source TEXT,event_id INTEGER,payload TEXT,PRIMARY KEY(source,event_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY,cursor INTEGER NOT NULL)')
        self.db.commit()
    def cursor(self,source):
        row=self.db.execute('SELECT cursor FROM cursors WHERE source=?',(source,)).fetchone()
        return row[0] if row else 0
    def append(self,source,rows):
        if not source: raise ValueError('Explicit source generation required')
        added=[]
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            cursor=self.cursor(source);previous=0
            for row in rows:
                event_id=row.get('id')
                if type(event_id) is not int or event_id<=previous: raise ValueError('Invalid/nonmonotonic event id')
                previous=event_id;payload=json.dumps(row,sort_keys=True,ensure_ascii=False)
                old=self.db.execute('SELECT payload FROM events WHERE source=? AND event_id=?',(source,event_id)).fetchone()
                if old:
                    if old[0]!=payload: raise ValueError('Source id collision: use new source generation after reset')
                    continue
                if event_id<=cursor: raise ValueError('Cursor would skip an unpersisted event')
                self.db.execute('INSERT INTO events VALUES(?,?,?)',(source,event_id,payload));cursor=event_id;added.append(row)
            self.db.execute('INSERT INTO cursors VALUES(?,?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor',(source,cursor))
        return added
    def close(self):
        self.db.close()


def read_events(defaults_file,after):
    after=int(after)
    if after<0: raise ValueError('Negative cursor')
    sql="SELECT JSON_OBJECT('id',id,'ts',CAST(ts AS CHAR),'char_id',char_id,'kind',kind,'map',map,'x',x,'y',y,'a1',a1,'a2',a2) FROM residents.resident_events WHERE id > %d ORDER BY id LIMIT 200" % after
    p=subprocess.run(['mariadb','--defaults-extra-file='+defaults_file,'--batch','--raw','--skip-column-names'],input=sql,text=True,capture_output=True,timeout=8)
    if p.returncode: raise RuntimeError('Reader database connection/query failed (credential-bearing details withheld)')
    return [json.loads(l) for l in p.stdout.splitlines() if l]

def fetch_with_retry(defaults_file,after):
    for attempt in range(3):
        try:
            return read_events(defaults_file,after)
        except (RuntimeError,subprocess.TimeoutExpired):
            if attempt==2: raise RuntimeError('Reader unavailable after 3 bounded attempts; durable cursor unchanged') from None
            time.sleep(.25*(2**attempt))
    raise AssertionError('Unreachable retry state')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--defaults-file',required=True)
    ap.add_argument('--after',type=int,default=0)
    ap.add_argument('--duration',type=float,default=0,help='Seconds of observation; 0 = one batch')
    ap.add_argument('--store',help='SQLite durable inbox; stdout is only diagnostic')
    ap.add_argument('--source-id',help='Explicit generation; change after source DB reset')
    args=ap.parse_args()
    if args.store and (not args.source_id or args.after): ap.error('store requires source-id and after=0')
    if args.after<0 or not 0<=args.duration<=300: ap.error('cursor >=0; duration 0..300')
    os.umask(0o077)
    store=EventStore(args.store) if args.store else None
    cursor=store.cursor(args.source_id) if store else args.after
    deadline=time.monotonic()+args.duration
    try:
        while True:
            rows=fetch_with_retry(args.defaults_file,cursor)
            if store:
                rows=store.append(args.source_id,rows)
                cursor=store.cursor(args.source_id)
            for row in rows:
                if not store:
                    if row['id']<=cursor: raise RuntimeError('Nonmonotonic source cursor')
                    cursor=row['id']
                print(json.dumps(row,ensure_ascii=False),flush=True)
            if time.monotonic()>=deadline:break
            time.sleep(min(.5,max(0,deadline-time.monotonic())))
    finally:
        if store: store.close()

if __name__=='__main__':main()
