"""Read static warps from explicit pinned rAthena NPC manifests.
Never invent NPC dialogue or alter the server. Scripted OnTouch transitions
must be supplied separately from inspected source, not guessed as talk steps.
"""
import pathlib,re
WARP=re.compile(r'^([\w@-]+),(\d+),(\d+),\d+\s+warp2?\s+[^\s]+\s+\d+,\d+,([\w@-]+),(\d+),(\d+)\s*(?://.*)?$')
def read_warps(root,manifests):
 root=pathlib.Path(root).resolve();seen=set();rows=set()
 def visit(relative):
  p=(root/relative).resolve()
  if not p.is_relative_to(root):raise ValueError('NPC source outside pinned root')
  if p in seen:return
  seen.add(p)
  for line in p.read_text(encoding='utf-8',errors='surrogateescape').splitlines():
   line=line.strip()
   if not line or line.startswith('//'):continue
   if p.suffix=='.conf':
    m=re.fullmatch(r'(?:npc|import):\s*(\S+)\s*(?://.*)?',line)
    if m:visit(m[1])
   else:
    m=WARP.fullmatch(line)
    if m:rows.add(' '.join(m.groups()))
 for manifest in manifests:visit(manifest)
 return sorted(rows)
