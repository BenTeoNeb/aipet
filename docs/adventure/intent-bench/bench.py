"""The benchmark: `uv run python bench.py CASES.json MODEL VARIANT [split]`.
Asks the decision model (cached per model, variant and case), plays its actions, compares the
engine's result with the gold's. Reports raw accuracy and precision when it decides (confidence)."""
import hashlib, json, os, sys, time, concurrent.futures as cf
from pathlib import Path
import httpx
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dec_lib import context, play, world, state
from aipet import engines
from aipet.config import load_config
from aipet.games.adventure_engine import _key

KEY = engines._api_key(engines.settings(load_config(), "conversation"), "openrouter")
CACHE = Path.home() / ".aipet" / "cache" / "intent-bench"  # answers kept per request: rerunning costs nothing
CACHE.mkdir(parents=True, exist_ok=True)
DESC = {"go": "aller, se déplacer, entrer, sortir, retourner quelque part", "take": "prendre, ramasser, attraper, cueillir",
        "drop": "poser, lâcher", "give": "donner, offrir, montrer une chose à quelqu'un",
        "use": "utiliser une chose (sur une autre), mettre, peindre, frotter", "combine": "assembler, attacher, mélanger deux choses",
        "open": "ouvrir", "close": "fermer", "talk": "parler à quelqu'un, saluer, appeler", "ask": "demander quelque chose à quelqu'un",
        "answer": "répondre à une devinette ou un mot de passe", "look": "regarder autour, décrire le lieu",
        "examine": "regarder de près une chose", "search": "fouiller, chercher ici", "inventory": "ce qu'il y a dans son sac",
        "push": "pousser, appuyer, presser (un bouton)", "pull": "tirer", "light": "allumer, éclairer",
        "throw": "lancer, jeter (vers quelqu'un)", "hit": "taper, frapper", "destroy": "casser, effacer, détruire pour de bon",
        "eat": "manger", "drink": "boire", "climb": "grimper, monter dans ou sur une chose", "swim": "nager, plonger",
        "jump": "sauter", "touch": "toucher, caresser, chatouiller", "listen": "écouter", "smell": "sentir, renifler",
        "fill": "remplir", "read": "lire", "wear": "porter, enfiler", "where": "demander où est une chose",
        "help": "demander de l'aide, un indice", "wait": "attendre", "sit": "s'asseoir", "sing": "chanter", "dance": "danser",
        "other": "autre geste imaginaire"}
SLOTS = 3

def questions(c, variant):
    ctx = context(c)
    things = {i: n for i, n in ctx["à_portée"].items() if not n.startswith("→")}
    ways = {i: n for i, n in ctx["à_portée"].items() if n.startswith("→")}
    far = {p: f"lieu déjà vu : {n}" for p, n in ctx["lieux_connus"].items() if p not in ways}
    none = {"aucun": "aucune chose de la liste pour cette action (rien, ou une chose imaginaire)"}
    state_txt = (
        f"Un enfant de 5 à 8 ans joue à voix haute (reconnaissance vocale, phrases parfois maladroites) à une aventure dont il est le héros.\n"
        f"Lieu : {ctx['lieu']}.\n"
        f"Le narrateur vient de dire : « {ctx['narrateur']} »\n"
        + (f"Juste avant, l'enfant avait dit : « {ctx['dit_avant']} »\n" if ctx["dit_avant"] else "")
        + f"Choses et personnages à portée (id = nom) : {'; '.join(f'{i} = {n}' for i, n in things.items()) or 'aucun'}\n"
        f"Chemins d'ici (id = nom) : {'; '.join(f'{i} = {n}' for i, n in ways.items()) or 'aucun'}\n"
        f"Lieux déjà vus, plus loin (id = nom) : {'; '.join(f'{i} = {n}' for i, n in far.items()) or 'aucun'}\n"
        + (f"Devinette posée ici par : {', '.join(ctx['devinettes_ici'])}\n" if ctx["devinettes_ici"] else "")
        + f"\nL'enfant dit maintenant : « {ctx['enfant']} »\n\n"
        "Découpe sa phrase en actions de jeu, dans l'ordre. Règles : parler à, toucher, regarder quelqu'un → objet = "
        "la personne ; donner, lancer, utiliser X sur Y → objet = X, cible = Y ; aller quelque part → vers = le lieu "
        "(voisin ou déjà vu) ; « lui », « y », « ça » renvoient à ce que le narrateur ou l'enfant vient de nommer ; "
        "une réponse à une devinette → answer, cible = qui la pose ; un code de couleurs ou de boutons → une action "
        "push par bouton. Chaque action n'a que ses propres choses. Bavardage ou question hors du jeu → 0 action.")
    q = {"nombre": {"type": "choice", "instructions": "combien d'actions de jeu la phrase demande",
                    "criteria": {"0": "aucune : bavardage, question hors du jeu", "1": "une action", "2": "deux actions",
                                 "3": "trois actions ou plus"}}}
    for n in range(1, SLOTS + 1):
        q[f"a{n}_verbe"] = {"type": "choice", "instructions": f"le verbe de l'action {n}", "criteria": {**DESC, "rien": f"pas d'action {n}"}}
        q[f"a{n}_objet"] = {"type": "choice", "instructions": f"l'objet de l'action {n} (la chose prise, donnée, utilisée, regardée, ou la personne à qui on parle)",
                            "criteria": {**things, **none, "autre": "une chose qui n'est pas dans la liste"}}
        q[f"a{n}_cible"] = {"type": "choice", "instructions": f"la cible de l'action {n} (à qui on donne, sur quoi on utilise ou lance), sinon aucun",
                            "criteria": {**things, **none, "autre": "une chose qui n'est pas dans la liste"}}
        q[f"a{n}_vers"] = {"type": "choice", "instructions": f"le lieu où va l'action {n} si c'est un déplacement, sinon aucun",
                           "criteria": {**ways, **far, **none, "autre": "un lieu inconnu"}}
    if variant == "v4":
        return state_txt, q
    if variant == "v5":
        state_txt = state_txt.replace("Bavardage ou question hors du jeu → 0 action.", (
            "Un déplacement dit en plusieurs mots (« je monte l'échelle et je vais sur la terrasse », « je sors et je "
            "vais au pont ») = UNE action go vers le lieu final. Demander de l'aide ou un indice à Flocon = l'action help. "
            "Regarder une chose précise = examine avec cette chose ; regarder autour = look. Dans « je l'allume au feu », "
            "« l' » est l'objet (ce qu'on allume) et le feu la cible. Une devinette : answer, objet = aucun, cible = qui "
            "la pose. Bavardage ou question hors du jeu → 0 action. Si la phrase parle de « ça », « le truc », « le "
            "machin » sans que rien ne soit nommé avant, ou si elle est incompréhensible → « pas clair »."))
        q["nombre"]["criteria"]["?"] = "pas clair : on ne sait pas de quoi il parle, il faut lui demander"
        return state_txt, q
    raise SystemExit(f"unknown variant {variant}")

def ask_llm(model, variant, c):
    """A chat model with structured output, from the same context and options (the comparison)."""
    state_txt, q = questions(c, variant)
    def enum(k): return list(q[k]["criteria"])
    act = {"type": "object", "additionalProperties": False, "required": ["verb", "object", "target", "to"],
           "properties": {"verb": {"type": "string", "enum": enum("a1_verbe")}, "object": {"type": "string", "enum": enum("a1_objet")},
                          "target": {"type": "string", "enum": enum("a1_cible")}, "to": {"type": "string", "enum": enum("a1_vers")}}}
    schema = {"type": "object", "additionalProperties": False, "required": ["actions"],
              "properties": {"actions": {"type": "array", "items": act}}}
    body = {"model": model, "messages": [{"role": "user", "content": state_txt + "\n\nRéponds par la liste des actions (vide si aucune), avec les ids."}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "intent", "strict": True, "schema": schema}},
            "reasoning": {"effort": "minimal"}, "provider": {"data_collection": "deny"}}
    h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    f = CACHE / f"{h}.json"
    if f.exists():
        return json.loads(f.read_text())
    t = time.time()
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=60, headers={"Authorization": f"Bearer {KEY}"}, json=body)
    if r.status_code != 200:
        return {"error": r.text[:300]}
    j = r.json()
    acts = json.loads(j["choices"][0]["message"]["content"])["actions"]
    a = {"nombre": {"choice": str(min(len(acts), 3)), "confidence": 1}}
    for i, x in enumerate(acts[:3], 1):
        a[f"a{i}_verbe"] = {"choice": x["verb"]}; a[f"a{i}_objet"] = {"choice": x["object"]}
        a[f"a{i}_cible"] = {"choice": x["target"]}; a[f"a{i}_vers"] = {"choice": x["to"]}
    out = {"answers": a, "secs": time.time() - t, "cost": j.get("usage", {}).get("cost", 0)}
    f.write_text(json.dumps(out, ensure_ascii=False))
    return out

def ask(model, variant, c):
    if model.startswith("llm:"):
        return ask_llm(model[4:], variant, c)
    state_txt, q = questions(c, variant)
    body = {"model": model, "state": state_txt, "questions": q, **({"provider": {"zdr": True}} if model.startswith("typesafe/") else {})}
    h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    f = CACHE / f"{h}.json"
    if f.exists():
        return json.loads(f.read_text())
    for attempt in range(3):
        t = time.time()
        r = httpx.post("https://openrouter.ai/api/alpha/decisions", timeout=60, headers={"Authorization": f"Bearer {KEY}"}, json=body)
        if r.status_code == 200:
            j = r.json(); out = {"answers": j["answers"], "secs": time.time() - t, "cost": j["usage"].get("cost", 0)}
            f.write_text(json.dumps(out, ensure_ascii=False))
            return out
        time.sleep(2)
    return {"error": r.text[:300]}

FIELDS_OF = {"go": {"to"}, "climb": {"object", "to"}, "swim": {"to"}, "jump": {"to"}, "answer": {"object", "target"},
             "look": set(), "inventory": set(), "help": set(), "wait": set(), "dance": set(), "sing": {"object"}, "sit": set()}

def decision(res, said=""):
    """(actions, confidence) from the answers; actions [] = no game action. Each verb keeps only its
    fields; an answer is the child's own words."""
    a = res["answers"]
    if a["nombre"]["choice"] == "?":
        return [], a["nombre"].get("confidence", 1)
    n = int(a["nombre"]["choice"]); conf = a["nombre"].get("confidence", 1)
    acts = []
    for i in range(1, n + 1):
        v = a[f"a{i}_verbe"]["choice"]
        if v == "rien":
            break
        x = {"verb": v}
        for k, kk in (("objet", "object"), ("cible", "target"), ("vers", "to")):
            val = a[f"a{i}_{k}"]["choice"]
            conf = min(conf, a[f"a{i}_{k}"].get("confidence", 1))
            if val not in ("aucun", "autre"):
                x[kk] = val
        if x.get("target") and x.get("target") == x.get("object"):
            x.pop("target")
        if v == "answer":
            x = {"verb": "answer", "object": said, "target": x.get("target") or x.get("object", "")}
        keep = FIELDS_OF.get(v, {"object", "target"})
        x = {k: val for k, val in x.items() if k == "verb" or k in keep}
        conf = min(conf, a[f"a{i}_verbe"].get("confidence", 1))
        acts.append(x)
    return acts, conf

def correct(c, acts):
    g = c["gold"]
    w, st = world(c["title"]), state(c)
    k0 = _key(st, w)
    if g["kind"] != "act":
        return not acts  # hors_jeu, preciser: no game action (the narrator talks, or asks)
    if not acts:
        return False
    k1, _, t1 = play(c, acts)
    k2, _, t2 = play(c, g["actions"])
    return k1 == k2 and t1 == t2  # the same world after, and the same told to the child

def main():
    cases = json.load(open(sys.argv[1])); model, variant = sys.argv[2], sys.argv[3]
    with cf.ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda c: ask(model, variant, c), cases))
    rows = []
    for c, r in zip(cases, res):
        if "error" in r:
            print("ERR", r["error"][:200]); continue
        acts, conf = decision(r, c['said'])
        rows.append({"case": c, "acts": acts, "conf": conf, "ok": correct(c, acts), "secs": r["secs"], "cost": r["cost"]})
    report(rows, f"{model} {variant}")
    json.dump([{"said": x["case"]["said"], "src": x["case"]["src"], "gold": x["case"]["gold"], "acts": x["acts"], "conf": x["conf"], "ok": x["ok"]} for x in rows],
              open(f"{sys.argv[1]}.{model.replace('/', '_')}.{variant}.json", "w"), ensure_ascii=False, indent=1)

def report(rows, label):
    import statistics as S
    n = len(rows)
    print(f"== {label} : {n} cas")
    for src in sorted({r["case"]["src"] for r in rows}):
        sub = [r for r in rows if r["case"]["src"] == src]
        print(f"   {src:8} justes {sum(r['ok'] for r in sub)}/{len(sub)}")
    for kind in ("act", "hors_jeu", "preciser"):
        sub = [r for r in rows if r["case"]["gold"]["kind"] == kind]
        if sub: print(f"   {kind:8} justes {sum(r['ok'] for r in sub)}/{len(sub)}")
    print(f"   TOTAL justes {sum(r['ok'] for r in rows)}/{n} = {100*sum(r['ok'] for r in rows)/n:.1f}%")
    for th in (0.5, 0.7, 0.8, 0.9, 0.95):
        dec = [r for r in rows if r["conf"] >= th]
        if dec: print(f"   seuil {th}: décide {len(dec)}/{n} ({100*len(dec)/n:.0f}%), précision {100*sum(r['ok'] for r in dec)/len(dec):.1f}%")
    print(f"   latence médiane {S.median(r['secs'] for r in rows):.2f}s p90 {sorted(r['secs'] for r in rows)[int(.9*n)]:.2f}s ; coût {sum(r['cost'] for r in rows):.4f}$")

if __name__ == "__main__":
    main()
