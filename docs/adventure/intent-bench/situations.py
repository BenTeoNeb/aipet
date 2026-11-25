"""~8 situations per bank adventure: states along random walks (fair moves), at several depths."""

import json
import random
import sys

sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
from dec_lib import graphs, world

from aipet.games.adventure_engine import _moves

out = []
for title in sorted(graphs()):
    if "nouvelle version" in title:
        continue
    w = world(title)
    rng = random.Random(title)
    picks = []
    for _walk in range(4):
        st = w.start()
        depth = rng.choice([4, 8, 14, 22])
        for d in range(depth + 1):
            if d in (depth // 2, depth) and not st.ending:
                picks.append(st.copy())
            moves = [m for m in _moves(w, st, reduce=False) if not m[1].ending]
            if not moves:
                break
            st = rng.choice(moves)[1]
    for st in picks[:8]:
        # what the narrator just said: the place as described (the narrator's words are not known here)
        d = w.describe(st)
        told = " ".join(str(d.get(k, "")) for k in ("lieu", "description") if d.get(k))
        out.append({"src": "opus", "title": title, "said": "", "before": "", "told": told[:500], "state": st.to_dict()})
json.dump(out, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
print(len(out), "situations")
