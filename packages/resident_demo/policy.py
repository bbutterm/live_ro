import re
ROLES={'resident_a':('ResidentA','Мечник. Прямой, смелый, любит охоту, предлагает друзьям помощь.'),'resident_b':('Mira','Торговка. Общительная, деловая, интересуется добычей и запасами, шутит.'),'resident_c':('Borin','Послушник. Осторожный, добрый, интересуется самочувствием других.')}
def validate(d,resident):
 if resident not in ROLES or not isinstance(d,dict) or set(d)-{'action','text','memory','reason'}:raise ValueError('INVALID_DECISION')
 if d.get('action') not in ('speak','stroll','meet','rest','wait','hunt','visit_shop','sell_loot'):raise ValueError('INVALID_ACTION')
 if d['action']=='hunt' and resident!='resident_a':raise ValueError('ROLE_ACTION')
 for k,cap in [('text',120),('memory',400),('reason',300)]:
  x=d.get(k,'')
  if not isinstance(x,str) or len(x)>cap or re.search(r'[\x00-\x1f\x7f]',x):raise ValueError('INVALID_TEXT')
 if d['action']=='speak' and (not d.get('text','').strip() or d['text'].lstrip().startswith(('@','#','/'))):raise ValueError('NO_COMMANDS')
 return d

def perceive(t,rows):
 return [r for r in rows if r['map']==t['map'] and max(abs(r['x']-t['pos']['x']),abs(r['y']-t['pos']['y']))<=14]
