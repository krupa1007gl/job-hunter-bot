"""Мини-хелпер оркестратора: python3 .studio/set_task.py T-001 verified "evidence" [files...]"""
import re, sys
tid, status, ev, *files = sys.argv[1:]
p = ".studio/projects/job-hunter-bot/state.toml"
s = open(p).read()
a = s.index(f'id = "{tid}"'); b = s.find("\n[[", a); b = len(s) if b < 0 else b
blk = s[a:b]
blk = re.sub(r'^status = ".*"$', f'status = "{status}"', blk, flags=re.M)
if ev:
    blk = re.sub(r'^evidence = \[(.*)\]$', lambda m: 'evidence = [' + (m.group(1) + ', ' if m.group(1) else '') + '"' + ev.replace('"', "'") + '"]', blk, flags=re.M)
if files:
    blk = re.sub(r'^files = \[.*\]$', 'files = [' + ', '.join(f'"{f}"' for f in files) + ']', blk, flags=re.M)
open(p, "w").write(s[:a] + blk + s[b:])
