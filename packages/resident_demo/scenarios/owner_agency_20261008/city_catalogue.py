"""Read-only geometry admission; no repair or side effects."""
import collections,gzip,struct,hashlib
from pathlib import Path

def admit(candidates,starts,files,completed_goal=None):
    blobs=[Path(p).read_bytes() for p in files]
    assert blobs and all(b==blobs[0] for b in blobs),'FIELD_MISMATCH'
    data=gzip.decompress(blobs[0]);width,height=struct.unpack('<HH',data[:4]);cells=data[4:]
    assert len(cells)==width*height,'INVALID_FIELD_SIZE'
    def walk(p):
        x,y=p;return 0<=x<width and 0<=y<height and bool(cells[y*width+x]&1)
    lengths={};accepted={}
    for goal,target in candidates.items():
        if goal==completed_goal or not walk(target):continue
        distances={}
        for actor,start in starts.items():
            start=tuple(start);assert walk(start),'INVALID_START'
            if max(abs(start[i]-target[i]) for i in (0,1))<4:break
            queue=collections.deque([(start,0)]);seen={start}
            while queue:
                point,distance=queue.popleft()
                if point==target:distances[actor]=distance;break
                x,y=point
                for point2 in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                    if point2 not in seen and walk(point2):seen.add(point2);queue.append((point2,distance+1))
            if actor not in distances:break
        if set(distances)==set(starts):accepted[goal]=target;lengths[goal]=distances
    assert accepted,'NO_FEASIBLE_NEW_SHARED_GOAL'
    return accepted,{'field_sha256':hashlib.sha256(blobs[0]).hexdigest(),'connected_path_lengths':lengths,'completed_goal_excluded':completed_goal}
