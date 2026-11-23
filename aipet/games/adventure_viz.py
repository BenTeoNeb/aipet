"""Adventure explorer: one standalone page to see, walk through and debug the adventures.

`pet-adventures [out.html]` writes a self-contained page (the data is embedded; the layout library
comes from a CDN), also served at /debug/adventures. For each adventure of the bank and each stored
one (invented for a child):

- the map: the places, what is in each (objects, characters, the scripted actions), the ways
  between them and what locks them;
- the puzzle chain: what unlocks what, up to every ending (an arrow per requirement, dashed when
  another way exists), so every road to a win or a loss shows at once;
- the shortest way to every ending, step by step, with the hero's bag and flags at each step;
- the solver's checks: errors, states, actions that can never happen, places never reached;
- the games really played: the logged `adventure_act` calls replayed through the engine (a
  different outcome than the logged one means the engine or the world changed since).
"""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path

from aipet import paths
from aipet.fileio import load_json
from aipet.games.adventure import LIBRARY, drafts, library
from aipet.games.adventure_engine import (
    MAX_STATES,
    VERBS,
    State,
    World,
    _key,
    _moves,
    check,
    exit_rules,
    of,
    useful_steps,
)
from aipet.games.adventure_patterns import PATTERNS
from aipet.memory.cards import DIR_ADVENTURES
from storyworld.model import format_of

TEMPLATE = Path(__file__).parent.parent / "static" / "adventures.html"
MODEL_DIR = paths.ROOT / "docs" / "adventure" / "model"  # reference adventures (data model work)
CONTINUE = {"done", "hint", "free"}  # what game.act goes on after (see AdventureGame.act)


# ---- names ---------------------------------------------------------------------------------------
def _name(w: World, ident: str | None) -> str:
    return w.name(ident) if ident else ""


def trigger_label(w: World, t: dict) -> str:
    obj, tgt = _name(w, t.get("object")), _name(w, t.get("target"))
    return f"{t.get('verb', '?')} {obj}" + (f" → {tgt}" if tgt else "")


def action_label(w: World, a: dict) -> str:
    """« hit / use le grand tambour »: its verbs, then what its triggers are about."""
    ts = a.get("triggers") or [{}]
    verbs = list(dict.fromkeys(t.get("verb", "?") for t in ts))
    things = list(dict.fromkeys(trigger_label(w, t).split(" ", 1)[1] for t in ts if " " in trigger_label(w, t)))
    return f"{' / '.join(verbs)} {things[0] if things else ''}".strip() + (
        f" (+{len(things) - 1})" if len(things) > 1 else ""
    )


def step_label(w: World, step: tuple) -> str:
    kind, ident = step
    if kind == "go":
        return f"aller : {w.name(ident)}"
    if kind == "take":
        return f"prendre {w.name(ident)}"
    if kind == "open":
        return f"ouvrir {w.name(ident)}"
    if kind == "press":
        return f"appuyer sur {w.name(ident)}"
    if kind == "answer":
        return f"répondre à {w.name(ident)} : « {w.thing(ident)['answer']['accept'][0]} »"
    return action_label(w, next((x for x in w.actions if x["id"] == ident), {}))


def _snapshot(w: World, st: State) -> dict:
    return {
        "place": st.place,
        "inventory": list(st.inventory),
        "flags": list(st.flags),
        "visited": list(st.visited),
        "ending": st.ending,
        "items_at": dict(st.items_at),
        "chars_at": dict(st.chars_at),
        "done": list(st.done_actions),
        "props": {i: {k: w.prop(st, i, k) for k in ("open", "locked", "state", "answered", "follows", "pressed")}
                  | ({"uses": w.uses_left(st, i)} if w.max_uses(i) else {})
                  for i in [*w.items, *w.chars]
                  if w.thing(i).get("openable") or w.thing(i).get("states") or i in w.riddles or i in w.chars
                  or w.max_uses(i)},
        "dark": w.dark(st),
        "handled": list(st.handled),
        "names": {i: w.name(i, st) for i in [*w.items, *w.chars] if w.thing(i).get("states")},
    }  # fmt: skip


# ---- exploration ---------------------------------------------------------------------------------
def _advance(w: World, st: State, step: tuple) -> State:
    """The state after one solver step: the solver's own move (`_moves`), so they never disagree."""
    for s, n in _moves(w, st, reduce=False):
        if s == step:
            return n
    raise ValueError(f"step {step} is not possible here")


def explore(w: World, max_states: int = MAX_STATES) -> dict:
    """Every reachable state: the first (shortest) way to each ending, which actions can happen,
    which lie on a road to a win, which lose, and the places ever reached."""
    s0 = w.start()
    k0 = _key(s0, w)
    states: dict[tuple, State] = {k0: s0}
    parent: dict[tuple, tuple] = {k0: (None, None)}
    edges: list[tuple] = []
    queue = deque([k0])
    first_end: dict[str, tuple] = {}
    while queue and len(states) < max_states:
        k = queue.popleft()
        st = states[k]
        if st.ending:
            first_end.setdefault(st.ending, k)
            continue
        for step, n in _moves(w, st, reduce=True):
            nk = _key(n, w)
            edges.append((k, step, nk))
            if nk not in states:
                states[nk], parent[nk] = n, (k, step)
                queue.append(nk)
    wins = {e for e, x in w.endings.items() if x.get("kind", "win") == "win"}
    good = {k for k, s in states.items() if s.ending in wins}
    reverse: dict[tuple, list[tuple]] = {}
    for a, _, b in edges:
        reverse.setdefault(b, []).append(a)
    todo = deque(good)
    while todo:
        for a in reverse.get(todo.popleft(), []):
            if a not in good:
                good.add(a)
                todo.append(a)
    fired, on_win, losing = set(), set(), set()
    for a, (kind, ident), b in edges:
        if kind != "act":
            continue
        fired.add(ident)
        if a in good and b in good:
            on_win.add(ident)
        if states[b].ending and states[b].ending not in wins:
            losing.add(ident)
    roads = {}
    for ending, k in first_end.items():
        steps: list[tuple] = []
        at: tuple = k
        while parent[at][0] is not None:
            at, step = parent[at]
            steps.append(step)
        roads[ending] = useful_steps(w, steps[::-1])
    return {
        "states": len(states),
        "complete": not queue,
        "fired": sorted(fired),
        "on_win": sorted(on_win),
        "losing": sorted(losing),
        "reached": sorted({s.place for s in states.values()}),
        "stuck": len([k for k, s in states.items() if not s.ending and k not in good]),
        "roads": roads,
    }


def walk(w: World, steps: list[tuple]) -> list[dict]:
    """A road step by step, with the state after each step."""
    st, out = w.start(), []
    for step in steps:
        st = _advance(w, st, step)
        a: dict = next((x for x in w.actions if x["id"] == step[1]), {}) if step[0] == "act" else {}
        out.append({"kind": step[0], "id": step[1], "label": step_label(w, step), "text": a.get("text", ""),
                    "state": _snapshot(w, st)})  # fmt: skip
    return out


# ---- where things are ----------------------------------------------------------------------------
def free_places(w: World) -> set[str]:
    """Places reached from the start without meeting any condition (doors closed count)."""
    start = w.g.get("start", {}).get("place")
    seen, todo = {start}, [start]
    while todo:
        for x in w.places.get(todo.pop(), {}).get("exits", []):
            if not exit_rules(x) and x.get("to") in w.places and x["to"] not in seen:
                seen.add(x["to"])
                todo.append(x["to"])
    return seen


def place_of(w: World, ident: str) -> str:
    """Where a thing starts: its place, or its container's place ("" = nowhere yet)."""
    seen = set()
    while ident and ident not in w.places and ident not in seen:
        seen.add(ident)
        ident = w.thing(ident).get("at", "")
    return ident if ident in w.places else ""


def action_place(w: World, a: dict) -> str:
    """Where an action happens: its place, else where its triggers' things start ("" = anywhere)."""
    if a.get("place") in w.places:
        return a["place"]
    things = [x for t in a.get("triggers", []) for x in (t.get("object"), t.get("target")) if x]
    fixed = [x for x in things if x in w.chars or not w.items.get(x, {}).get("takeable", True)]
    for x in fixed + things:  # what can't be carried says where it happens (the lamp, not the jar)
        if p := place_of(w, x):
            return p
    return ""


# ---- the puzzle chain ----------------------------------------------------------------------------
def chain(w: World) -> dict:
    """What unlocks what (a puzzle dependency chart): nodes start, act: (an action, any of its
    triggers), take:, open: (a door or container to open), reach: (a place behind a condition),
    end:; an edge per requirement. A requirement with several producers, or that only some
    triggers of an action have, is marked `alt` (another way exists)."""
    free = free_places(w)
    start_inv = set(w.g.get("start", {}).get("inventory", []))
    nodes: dict[str, dict] = {"start": {"id": "start", "kind": "start", "label": "Départ"}}
    edges: dict[tuple[str, str], dict] = {}
    todo: list[str] = []

    def node(nid: str, **kw) -> str:
        if nid not in nodes:
            nodes[nid] = {"id": nid, **kw}
            todo.append(nid)
        return nid

    def acts(pred) -> list[str]:
        riddles = [node(f"answer:{r}", kind="answer", ref=r, label=f"devinette {of(w.name(r))}")
                   for r in w.riddles if any(pred(e) for e in w.thing(r)["answer"].get("effects", []))]  # fmt: skip
        return [f"act:{a['id']}" for a in w.actions if any(pred(e) for e in a.get("effects", []))] + riddles

    def opened(ident: str) -> list[str] | None:
        item = w.items.get(ident, {})
        if not item.get("openable") or item.get("open"):
            return None
        return [node(f"open:{ident}", kind="open", ref=ident, label=f"ouvrir {w.name(ident)}"),
                *acts(lambda e: e == f"open:{ident}")]  # fmt: skip

    def producers(rule: str) -> list[str] | None:
        """Nodes that make `rule` true; None when it holds from the start."""
        if rule.startswith("!"):
            return None
        kind, _, rest = rule.partition(":")
        arg = rest.split(":")[0]
        if kind == "has":
            if arg in start_inv:
                return None
            out = acts(lambda e: e == f"gain:{arg}")
            if w.items.get(arg, {}).get("takeable", True) and arg in w.items:
                out.insert(0, node(f"take:{arg}", kind="take", ref=arg, label=f"prendre {w.name(arg)}"))
            return out
        if kind == "flag":
            return acts(lambda e: e == f"flag:{arg}")
        if kind == "open":
            return opened(arg)
        if kind == "is":
            if w.thing(arg).get("state") == rest.split(":")[-1]:
                return None
            return acts(lambda e: e == f"set:{rest}")
        if kind in ("at", "visited"):
            return reach(arg)
        if kind == "here":
            return present(arg)
        return None

    def reach(place: str) -> list[str] | None:
        if place in free or place not in w.places:
            return None
        return [node(f"reach:{place}", kind="reach", ref=place, label=f"accès : {w.name(place)}")]

    def present(ident: str) -> list[str] | None:
        """An item or character there to be used: revealed, moved, in a container to open, or
        behind a locked way."""
        if ident in start_inv:
            return None
        made = acts(lambda e: e in (f"reveal:{ident}", f"gain:{ident}") or e.startswith(f"move:{ident}:"))
        at = w.thing(ident).get("at", "")
        if at in w.items:  # in a container
            made = (opened(at) or []) + (present(at) or []) + made
        elif at:
            made = (reach(at) or []) + lit(at) + made
        return made or None

    def lit(place: str) -> list[str]:
        """In a dark place, what makes a light shine: having it, and its conditions (any light)."""
        if not w.places.get(place, {}).get("dark"):
            return []
        out = []
        for light in w.lights:
            rules = w.items[light]["light"] if isinstance(w.items[light]["light"], list) else []
            out += [n for r in [f"has:{light}", *rules] for n in producers(r) or []]
        return out

    def need(target: str, prods: list[str] | None, why: str, alt: bool = False):
        if not prods:
            return
        for p in prods:
            e = edges.setdefault((p, target), {"from": p, "to": target, "alt": False, "why": why})
            e["alt"] = e["alt"] or alt or len(prods) > 1

    for a in w.actions:
        node(f"act:{a['id']}", kind="act", ref=a["id"], label=action_label(w, a))
    for e in w.endings.values():
        nodes[f"end:{e['id']}"] = {"id": f"end:{e['id']}", "kind": "end", "ref": e["id"],
                                   "win": e.get("kind", "win") == "win", "label": e["id"]}  # fmt: skip
    while todo:
        nid = todo.pop()
        n = nodes[nid]
        if n["kind"] == "act":
            a = next(x for x in w.actions if x["id"] == n["ref"])
            shared = [(producers(r), r) for r in a.get("when", [])]
            if a.get("place"):
                shared.append((reach(a["place"]), f"at:{a['place']}"))
            for prods, why in shared:
                need(nid, prods, why)
            needed = {r[4:] for r in a.get("when", []) if r.startswith("has:")}
            wants = [[(present(x), f"là : {x}") for x in (t.get("object"), t.get("target")) if x and x not in needed]
                     for t in a.get("triggers", [])]  # fmt: skip
            for mine in wants:  # a thing only some triggers need: another way exists
                for prods, why in mine:
                    need(nid, prods, why, alt=not all(any(p == prods for p, _ in other) for other in wants))
            for e in a.get("effects", []):
                if e.startswith("end:"):
                    need(f"end:{e[4:]}", [nid], e)
        elif n["kind"] == "take":
            need(nid, present(n["ref"]), f"là : {n['ref']}")
        elif n["kind"] == "answer":
            need(nid, present(n["ref"]), f"là : {n['ref']}")
            for e in w.thing(n["ref"])["answer"].get("effects", []):
                if e.startswith("end:"):
                    need(f"end:{e[4:]}", [nid], e)
        elif n["kind"] == "code":
            for b in w.items[n["ref"]]["code"]["press"]:
                need(nid, present(b), f"là : {b}")
        elif n["kind"] == "open":
            item = w.items[n["ref"]]
            if item.get("answer"):
                label = f"devinette {of(w.name(n['ref']))}"
                need(nid, [node(f"answer:{n['ref']}", kind="answer", ref=n["ref"], label=label)], "answer")
            elif item.get("code"):
                need(nid, [node(f"code:{n['ref']}", kind="code", ref=n["ref"], label=f"le code {of(w.name(n['ref']))}")],
                     "code")  # fmt: skip
            elif item.get("locked") and item.get("key"):
                need(nid, producers(f"has:{item['key']}"), f"has:{item['key']}")
            elif item.get("locked"):  # no key: an action unlocks or opens it
                ref = n["ref"]
                need(nid, acts(lambda e, r=ref: e in (f"unlock:{r}", f"open:{r}")), f"unlock:{ref}")
            doors = {x.get("door") for p in w.places.values() for x in p.get("exits", [])}
            if n["ref"] not in doors:
                need(nid, present(n["ref"]), f"là : {n['ref']}")
        elif n["kind"] == "reach":
            place = n["ref"]
            for p in w.places.values():
                for x in p.get("exits", []):
                    if x.get("to") != place:
                        continue
                    for r in exit_rules(x):
                        need(nid, producers(r), r)
                    need(nid, reach(p["id"]), f"depuis {p['id']}")
            need(nid, acts(lambda e, place=place: e == f"goto:{place}"), "goto")
    for nid, n in nodes.items():  # roots hang off the start
        if nid != "start" and n["kind"] != "end" and not any(e["to"] == nid for e in edges.values()):
            edges[("start", nid)] = {"from": "start", "to": nid, "alt": False, "why": ""}
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


# ---- games really played (session logs) ----------------------------------------------------------
def _parse(result) -> dict:
    if isinstance(result, dict):
        return result
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return {"_raw": str(result)}


def _logged_outcomes(result: dict) -> list[str]:
    if "results" in result:
        return [r.get("outcome", "?") for r in result["results"]]
    return re.findall(r'"outcome": "(\w+)"', result.get("_raw", ""))


def played(worlds: dict[str, World], logs: Path) -> dict[str, list[dict]]:
    """Per title, the games found in the session logs, replayed through the engine."""
    games: dict[str, list[dict]] = {}
    last_state: dict[str, State] = {}
    checkpoint: dict[str, State] = {}
    for f in sorted(logs.glob("*.jsonl")) if logs.is_dir() else []:
        text = f.read_text(encoding="utf-8", errors="replace")
        if "adventure_" not in text:
            continue
        title, game, st, call, sim = None, None, None, None, None
        for line in text.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            name = ev.get("name", "")
            if ev.get("type") == "playtest_start":  # a simulated child (pet-playtest)
                sim = {"child": ev.get("child", ""), "style": ev.get("style", "")}
            elif ev.get("type") == "playtest_narrator" and game and game["turns"]:
                game["turns"][-1]["told"] = ev.get("text", "")
            elif ev.get("type") == "playtest_issue" and game and game["turns"]:
                game["turns"][-1].setdefault("issues", []).append({"kind": ev.get("kind"), "text": ev.get("text")})
            if ev.get("type") == "tool_call" and name.startswith("adventure_"):
                call = ev
                continue
            if ev.get("type") != "tool_result" or not call or name != call.get("name"):
                continue
            args, res = call.get("arguments") or {}, _parse(ev.get("result"))
            call = None
            if name == "adventure_start":
                got = res.get("title") or re.search(r'"title": "([^"]+)"', res.get("_raw", ""))
                got = got if isinstance(got, str) else (got.group(1) if got else None)
                if not got or got not in worlds:
                    continue
                title, w = got, worlds[got]
                mode = ("checkpoint" if args.get("from_checkpoint") else
                        "resume" if args.get("resume") else "replay" if args.get("replay") else "new")  # fmt: skip
                if mode == "checkpoint" and title in checkpoint:
                    st = State.from_dict(checkpoint.pop(title).to_dict())
                elif mode == "resume" and title in last_state:
                    st = State.from_dict(last_state[title].to_dict())
                else:
                    st = w.start()
                game = {"session": f.stem, "t": ev.get("t", ""), "mode": mode, "turns": [],
                        "start": _snapshot(w, st), **({"simulated": sim} if sim else {})}  # fmt: skip
                games.setdefault(title, []).append(game)
            elif name == "adventure_act" and game is not None and st is not None and title:
                w = worlds[title]
                intents, outcomes, fired = args.get("actions") or [{"verb": "look"}], [], []
                for intent in intents:
                    before = State.from_dict(st.to_dict())
                    r = w.act(st, intent)
                    outcomes.append(r.get("outcome", "?"))
                    fired += [x for x in st.done_actions if x not in before.done_actions]
                    if r.get("outcome") == "end" and r.get("ending") == "lose":
                        checkpoint[title] = before
                    if r.get("outcome") not in CONTINUE:
                        break
                logged = _logged_outcomes(res)
                game["turns"].append({
                    "t": ev.get("t", ""), "said": args.get("said", ""), "intents": intents, "outcomes": outcomes,
                    "logged": logged, "mismatch": bool(logged) and logged != outcomes, "fired": fired,
                    "state": _snapshot(w, st),
                })  # fmt: skip
                last_state[title] = st
            elif name == "adventure_end":
                game = None
    return games


# ---- everything --------------------------------------------------------------------------------
def describe(graph: dict, source: str, records: list[dict], games: list[dict]) -> dict:
    w = World(graph)
    errors, report = check(graph)
    ex = explore(w)
    roads = {e: walk(w, steps) for e, steps in ex.pop("roads").items()}
    used = {t.get("verb") for a in w.actions for t in a.get("triggers", [])}
    return {
        "source": source,
        "model": format_of(graph),  # version 1 graphs are shown migrated
        "graph": w.g,
        "names": {i: w.name(i) for i in [*w.places, *w.items, *w.chars]},
        "labels": {a["id"]: action_label(w, a) for a in w.actions},
        "trigger_labels": {a["id"]: [trigger_label(w, t) for t in a.get("triggers", [])] for a in w.actions},
        "places_of": {a["id"]: action_place(w, a) for a in w.actions},
        "item_places": {i: place_of(w, i) for i in w.items},
        # As the engine reads them (kinds' properties, states as a map, doors on both sides): the
        # page draws the map from these and shows the graph as written.
        "things": {
            i: {
                k: t.get(k)
                for k in (
                    "kind",
                    "takeable",
                    "openable",
                    "container",
                    "open",
                    "locked",
                    "key",
                    "at",
                    "state",
                    "states",
                    "uses",
                    "light",
                )
                if k in t
            }
            for i, t in [*w.items.items(), *w.chars.items()]
        },
        "doors_at": w.doors,
        "herrings": sorted(a["id"] for a in w.actions if w.red_herring(a)),
        "free_places": sorted(free_places(w)),
        "errors": errors,
        "report": {
            k: (sorted(v) if isinstance(v, set) else v)
            for k, v in report.items()
            if k in ("wins", "loses", "states", "complete", "shortest", "puzzles", "warnings")
        },  # fmt: skip
        "explore": ex,
        "roads": roads,
        "verbs": {v: VERBS[v] for v in sorted(used) if v in VERBS},
        "chain": chain(w),
        "records": records,
        "games": games,
    }


def collect(cfg: dict | None = None) -> dict:
    """The bank and the stored adventures (one entry per world: a stored copy of a bank adventure
    adds its records to that one)."""
    entries: dict[str, dict] = {}
    for g in library():
        entries[g["title"]] = {"graph": g, "source": "banque", "records": []}
    for g in drafts():  # the reference adventures of the data model (playable with adventure_drafts)
        entries[g["title"]] = {"graph": g, "source": "étalon", "records": []}
    store = paths.memory_dir(cfg) / DIR_ADVENTURES
    for p in sorted(store.glob("*.json")) if store.is_dir() else []:
        rec = load_json(p, None)
        if not isinstance(rec, dict) or not isinstance(rec.get("graph"), dict):
            continue
        g = rec["graph"]
        title = g.get("title") or rec.get("title") or p.stem
        if title in entries and entries[title]["graph"] == g:
            entry = entries[title]
        else:
            if title in entries:
                title = f"{title} ({rec.get('id', p.stem)})"
            entry = entries[title] = {"graph": g, "source": "inventée", "records": []}
        entry["records"].append({k: rec.get(k) for k in ("id", "player", "idea", "created", "updated", "runs",
                                                         "state", "checkpoints_left")})  # fmt: skip
    for name, (g, _) in PATTERNS.items():  # the catalogue of puzzle patterns
        entries[f"Patron · {name}"] = {"graph": {**g, "title": f"Patron · {name}"}, "source": "patron", "records": []}
    worlds = {t: World(e["graph"]) for t, e in entries.items()}
    games = played(worlds, paths.logs_dir() / "sessions")
    for title, more in played(worlds, paths.logs_dir() / "playtests").items():
        games.setdefault(title, []).extend(more)
    out = []
    for title, e in entries.items():
        d = describe(e["graph"], e["source"], e["records"], games.get(title, []))
        if e["source"] == "patron":  # tiny on purpose: below the size limits of a real adventure
            d["errors"] = [x for x in d["errors"] if not x.startswith(f"{e['graph']['difficulty']}:")]
        out.append({"title": title, **d})
    order = {"facile": 0, "moyen": 1, "difficile": 2}
    rank = {"étalon": 0, "banque": 1, "patron": 3}
    out.sort(key=lambda a: (rank.get(a["source"], 2), order.get(a["graph"].get("difficulty"), 3), a["title"]))
    return {"adventures": out, "bank": str(LIBRARY)}


def render(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*ADVENTURES_DATA*/null", blob, 1)


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description="Write the adventure explorer page.")
    ap.add_argument("out", nargs="?", default=str(paths.pet_dir() / "adventures.html"))
    out = Path(ap.parse_args(argv).out).expanduser()
    out.write_text(render(collect()), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
