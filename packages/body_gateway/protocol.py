"""Строгая сессия body v1: ACK не означает игровой результат."""
import uuid,math
class Session:
 def __init__(self,resident,char_id):
  self.resident=resident;self.char_id=char_id;self.epoch=None;self.seq=-1
 def accept(self,msg):
  if not isinstance(msg,dict) or msg.get('proto')!=1:raise ValueError('PROTOCOL')
  seq=msg.get('seq');ts=msg.get('ts');kind=msg.get('type');epoch=msg.get('body_epoch')
  if type(seq)!=int or seq<=self.seq or not isinstance(ts,(int,float)) or not math.isfinite(ts):raise ValueError('SEQUENCE_OR_TIMESTAMP')
  if kind=='hello':
   if msg.get('resident')!=self.resident or msg.get('char_id')!=self.char_id:raise ValueError('IDENTITY')
   if not isinstance(epoch,str):raise ValueError('EPOCH')
   uuid.UUID(epoch)
  elif kind=='telemetry':
   if self.epoch is None or epoch!=self.epoch:raise ValueError('EPOCH')
   if not isinstance(msg.get('map'),str) or not msg['map']:raise ValueError('MAP')
   for key in ('pos','dest'):
    if not isinstance(msg.get(key),dict) or any(type(msg[key].get(axis))!=int or not 0<=msg[key][axis]<=2048 for axis in ('x','y')):raise ValueError('POSITION')
  else:raise ValueError('MESSAGE_TYPE')
  self.epoch=epoch;self.seq=seq
  return msg
