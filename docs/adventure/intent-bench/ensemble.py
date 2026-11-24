import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dec_lib import play
base = sys.argv[1]; cases = json.load(open(base))
J = json.load(open(f"{base}.typesafe_jev-1.14.{sys.argv[2]}.json"))
L = json.load(open(f"{base}.openai_gpt-6-luna-decisions.{sys.argv[2]}.json"))
assert len(J) == len(L) == len(cases)
def res(c, acts):
    return ("none",) if not acts else play(c, acts)[0::2]
n = len(cases)
rows = []
for c, j, l in zip(cases, J, L):
    agree = res(c, j["acts"]) == res(c, l["acts"])
    rows.append((agree, j, l))
for tj, tl in ((0, 0), (0.5, 0), (0.5, 0.5), (0.7, 0), (0.7, 0.5)):
    dec = [(a, j, l) for a, j, l in rows if a and j["conf"] >= tj and l["conf"] >= tl]
    print(f"d'accord, conf Jev ≥ {tj}, Luna ≥ {tl}: décide {len(dec)}/{n} ({100*len(dec)/n:.0f}%), précision {100*sum(j['ok'] for _, j, _ in dec)/max(1,len(dec)):.1f}%")
# either: Jev confident, else Luna confident
for tj, tl in ((0.7, 0.7), (0.7, 0.8), (0.6, 0.8)):
    dec = []
    for a, j, l in rows:
        if j["conf"] >= tj: dec.append(j["ok"])
        elif l["conf"] >= tl: dec.append(l["ok"])
    print(f"Jev ≥ {tj} sinon Luna ≥ {tl}: décide {len(dec)}/{n} ({100*len(dec)/n:.0f}%), précision {100*sum(dec)/max(1,len(dec)):.1f}%")
