"""Checks gold labels: `uv run python validate.py labels.json` (cases with « gold »). For each case,
plays the gold actions on its state and reports what looks wrong: an id not in the adventure, an
unknown verb, « absent » (the thing is not in reach), a go that doesn't move."""
import json, sys
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
from dec_lib import world, state, play, expand
from aipet.games.adventure_engine import VERBS, canonical_verb
cases = json.load(open(sys.argv[1]))
bad = 0
for i, c in enumerate(cases):
    g = c.get("gold")
    if not g:
        print(f"[{i}] no gold"); bad += 1; continue
    if g.get("kind") != "act":
        continue
    w, st = world(c["title"]), state(c)
    ids = set(w.places) | set(w.items) | set(w.chars)
    problems = []
    for a in g.get("actions", []):
        v = a.get("verb", "")
        if v not in VERBS:
            problems.append(f"unknown verb {v}")
        for k in ("object", "target", "to"):
            x = a.get(k)
            if x and not (v == "answer" and k == "object") and x not in ids:
                problems.append(f"{k}={x} is not an id of the adventure")
    if not problems:
        key, outs, _ = play(c, g["actions"])
        if "absent" in outs:
            problems.append(f"outcomes {outs}: something is not in reach")
        for a in expand(w, state(c), g["actions"]):
            pass
    if problems:
        bad += 1
        print(f"[{i}] « {c['said'][:60]} » {g['actions']} → {'; '.join(problems)}")
print(f"{len(cases)} cases, {bad} to look at")
