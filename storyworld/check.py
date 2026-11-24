"""Validation: structure, references, rules, the solver's proof, the format's lints."""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from storyworld.chart import derive
from storyworld.hints import (
    useful_steps,
)
from storyworld.model import (
    ANSWER_FIELDS,
    CODE_FIELDS,
    KINDS,
    SENSE_FIELDS,
    SITUATED,
    STATE_FIELDS,
    TEXT_FIELDS,
    migrate,
)
from storyworld.profiles import (
    DIFFICULTIES,
)
from storyworld.rules import (
    CHARACTER,
    ENDING,
    FLAG,
    ITEM,
    OPENABLE,
    PLACE,
    STATE,
    THING,
    USABLE,
    args_of,
    parse,
    problem,
)
from storyworld.rules import CONDITIONS as CONDITION_KINDS
from storyworld.rules import EFFECTS as EFFECT_KINDS
from storyworld.schema import schema_errors
from storyworld.solver import (
    disagreements,
    solve,
)
from storyworld.verbs import (
    NEVER_SCRIPTED,
    OPENERS,
)
from storyworld.world import (
    World,
)

# ---- validation ---------------------------------------------------------------------------------
CONDITIONS = set(CONDITION_KINDS)
# A world that never goes back (no unflag, hide, close or lock): what was done stays done, so the
# hero is never locked out and the solver stays small (TextWorld builds its quests without cycles too).
EFFECTS = set(EFFECT_KINDS)


def _never_true(w: World, rule: str) -> bool:
    """A condition no effect can make true (and false at the start)."""
    r = parse(rule)
    neg, kind, args, a = r.negated, r.kind, r.args, r.arg
    if neg or not args:
        return False
    effects = [e for x in w.actions for e in x.get("effects", [])]
    st = w.start()
    if w.holds(st, [rule]):
        return False
    if kind == "has":
        reachable = (w.items.get(a, {}).get("at") or "") != "" and w.items.get(a, {}).get("takeable")
        return not reachable and f"gain:{a}" not in effects and f"reveal:{a}" not in effects
    if kind == "flag":
        return f"flag:{a}" not in effects
    if kind == "is":
        return f"set:{a}:{args[-1]}" not in effects
    if kind in ("open", "locked"):
        return not w.items.get(a, {}).get("openable")
    return False


def _builtin_twin(w: World, t: dict) -> str:
    """The openable thing a trigger opens anyway (« ouvrir le tiroir », « utiliser la clé sur la porte »):
    a scripted action there would answer instead of the built-in opening the solver counts on."""
    things = [x for x in (t.get("object"), t.get("target")) if x]
    box = next((x for x in things if w.items.get(x, {}).get("openable")), "")
    others = [x for x in things if x != box]
    if box and t.get("verb") in OPENERS | {"search"} and all(x == w.items[box].get("key") for x in others):
        return box
    return ""


@dataclass(frozen=True)
class Diagnostic:
    """One finding of `validate`: a stable `code` (« play.stuck », « trigger.reference »…), a
    message in English, and its severity: an error makes the adventure invalid, a warning is
    allowed but probably a mistake."""

    code: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return self.message


def check(graph: dict) -> tuple[list[str], dict]:
    """The pet's check: `validate` with the profile of the graph's `difficulty`. Errors as text (for
    the repair prompt and the logs), the report with its `warnings`."""
    profile = DIFFICULTIES.get(graph.get("difficulty", ""))
    diags, report = validate(graph, profile)
    errors = [] if profile else [f"difficulty must be one of {sorted(DIFFICULTIES)}"]
    return errors + [str(d) for d in diags if d.severity == "error"], report


def validate(graph: dict, profile: dict | None = None) -> tuple[list[Diagnostic], dict]:
    """Everything the format guarantees: structure and references, the rules well formed and naming
    what they should, and playability (a win can be reached, the hero is never stuck, the turn does
    what the solver counts on). With a `profile` (an application's policy: sizes, numbers of
    endings, steps, puzzles), its limits too. Returns the diagnostics and the solver report. The
    solver runs even when the structure has errors, so one repair sees them all."""
    diags: list[Diagnostic] = []

    def err(code: str, message: str):
        diags.append(Diagnostic(code, message))

    def warn(code: str, message: str):
        diags.append(Diagnostic(code, message, "warning"))

    try:
        shape = migrate(graph) if isinstance(graph, dict) else graph
    except Exception as exc:  # noqa: BLE001 — too broken to migrate: the schema says why
        shape = graph
        err("structure.unreadable", f"cannot migrate: {type(exc).__name__}")
    for pointer, message in schema_errors(shape):  # the format (the graph as migrated)
        err("schema", f"{pointer}: {message}")
    try:
        report = _semantics(graph, profile, err, warn)
    except Exception as exc:  # noqa: BLE001 — shapes the schema already reported: never raise
        if not any(d.code == "schema" for d in diags):
            raise
        err("structure.unreadable", f"cannot check further until the schema errors are fixed ({type(exc).__name__})")
        report = {}
    report["warnings"] = [str(d) for d in diags if d.severity == "warning"]
    return diags, report


def _semantics(graph: dict, profile: dict | None, err, warn) -> dict:
    """References, rules, kinds, playability and the profile's limits (see `validate`)."""
    w = World(graph, policy=profile)
    ids = set(w.places) | set(w.items) | set(w.chars)
    if graph.get("start", {}).get("place") not in w.places:
        err("start.place", "start.place is not a place id")
    if len(ids) != len(w.places) + len(w.items) + len(w.chars) or "" in ids:
        err("ids.unique", "ids must be unique and non-empty across places, items and characters")
    action_ids = [a.get("id") for a in w.actions]
    for dup in sorted({a for a in action_ids if action_ids.count(a) > 1 or not a}, key=str):
        err("action.id", f"action id {dup!r} is empty or used twice")
    for i in graph.get("start", {}).get("inventory", []):
        if i not in w.items:
            err("start.inventory", f"start inventory: unknown item {i}")
    for c in w.chars.values():
        if c.get("at") and c["at"] not in w.places:
            err("character.at", f"character {c['id']} is in unknown place {c['at']}")
    openable = {i for i, it in w.items.items() if it.get("openable")}
    holders = {i for i, it in w.items.items() if it.get("container")}
    on_exits = {x.get("door") for p in w.places.values() for x in p.get("exits", [])}
    for raw in graph.get("items", []):
        if raw.get("kind") is not None and raw["kind"] not in KINDS:
            err("item.kind", f"item {raw.get('id')}: kind {raw['kind']!r} is not one of {list(KINDS)}")
    for i in w.items.values():
        if i.get("at") and i["at"] not in w.places and i["at"] not in holders and i["at"] not in w.chars:
            err("item.at", f"item {i['id']} is in unknown place, container or character {i['at']}")
        if i.get("key") and i["key"] not in w.items:
            err("item.key", f"item {i['id']}: unknown key {i['key']}")
        if (i.get("locked") or i.get("key")) and not i.get("openable"):
            err("item.lock_kind", f"item {i['id']} is locked or has a key but is not a container or a door")
        if i["kind"] == "door" and i["id"] not in on_exits:
            err("door.unused", f"door {i['id']} closes no exit (name it in an exit's door)")
        for k in set(i.get("texts") or {}) - TEXT_FIELDS:
            err("item.texts", f"item {i['id']}: texts.{k} is not one of {sorted(TEXT_FIELDS)}")
        if "uses" in i and not w.max_uses(i["id"]) > 0:
            err("item.uses", f"item {i['id']}: uses is a number of times, 1 or more")
        if (i.get("texts") or {}).get("empty") and not w.max_uses(i["id"]):
            warn("item.empty_text", f"item {i['id']}: texts.empty is told when it has no use left, and it has no uses")
    for t in [*w.places.values(), *w.items.values()]:
        if "senses" in t and not isinstance(t["senses"], dict):
            err("senses.field", f"{t['id']}: senses is {{listen, smell, touch}}")
            continue
        for k in set(t.get("senses") or {}) - SENSE_FIELDS:
            err("senses.field", f"{t['id']}: senses.{k} is not one of {sorted(SENSE_FIELDS)}")
    if any(p.get("dark") for p in w.places.values()) and not w.lights:
        err("dark.no_light", "a place is dark, and no item gives light (light)")
    for conflict in w.mirrored:
        err("door.conflict", f"two doors on one way: {conflict}")
    for i in w.items.values():
        if not (code := i.get("code")):
            continue
        press = code.get("press") or []
        if not i.get("openable"):
            err("code.on", f"item {i['id']}: a code opens a container or a door")
        if len(press) < 2:
            err("code.presses", f"item {i['id']}: a code needs at least 2 presses")
        for b in press:
            if b not in w.items:
                err("code.button", f"item {i['id']}: code button {b} is not an item")
            elif w.items[b].get("takeable"):
                err(
                    "code.button_kind",
                    f"item {i['id']}: code button {b} must be scenery (kind scenery), not taken away",
                )
        for k in set(code) - CODE_FIELDS:
            err("code.field", f"item {i['id']}: code.{k} is not one of {sorted(CODE_FIELDS)}")
    owners: dict[str, set[str]] = {}
    for i in w.items.values():
        for b in (i.get("code") or {}).get("press", []):
            owners.setdefault(b, set()).add(i["id"])
    for b, o in owners.items():
        if len(o) > 1:
            err("code.shared_button", f"button {b} belongs to two codes")
    for t in [*graph.get("items", []), *graph.get("characters", [])]:
        states = t.get("states")
        listed = states if isinstance(states, list) else [{"id": k, **v} for k, v in (states or {}).items()]
        names = [s.get("id") for s in listed]
        if len(set(names)) != len(names) or not all(names):
            err("state.id", f"{t.get('id')}: every state needs its own id")
        for s in listed:
            for k in set(s) - STATE_FIELDS:
                err(
                    "state.field",
                    f"{t.get('id')}: state {s.get('id')}: {k} is not one of {sorted(STATE_FIELDS - {'id'})}",
                )
    for t in [*w.items.values(), *w.chars.values()]:
        if t.get("states") and t.get("state") not in t["states"]:
            err("state.start", f"{t['id']}: state {t.get('state')!r} is not one of its states {sorted(t['states'])}")

    def has_state(ident: str, state: str) -> bool:
        return state in (w.thing(ident).get("states") or {})

    def ids_for(what: str, prev: str) -> bool | set[str]:
        """The ids an argument may name (True: anything, a flag)."""
        table: dict[str, bool | set[str]] = {ITEM: set(w.items), OPENABLE: openable, CHARACTER: set(w.chars), PLACE: set(w.places),
                USABLE: {i for i in w.items if w.max_uses(i)},
                ENDING: set(w.endings), THING: set(w.items) | set(w.chars), FLAG: True,
                STATE: set(w.thing(prev).get("states") or {})}  # fmt: skip
        return table[what]

    def typed(where: str, text: str, table: dict, word: str):
        """One rule: well formed (kind, arity), and every argument names what its kind says."""
        if why := problem(text, table):
            err("rule.malformed", f"{where}: {word} {text!r}: {why}")
            return
        r = parse(text)
        for n, (arg, what) in enumerate(zip(r.args, table[r.kind].args, strict=True)):
            allowed = ids_for(what, r.args[n - 1] if n else "")
            if isinstance(allowed, set) and arg not in allowed:
                err("rule.reference", f"{where}: {word} {text}: unknown {what} {arg}")

    def check_effects(where: str, effects: list[str]):
        for e in effects or []:
            typed(where, e, EFFECT_KINDS, "effect")

    def rules(where: str, when: list[str]):
        for r in when or []:
            typed(where, r, CONDITION_KINDS, "condition")

    for p in w.places.values():
        for x in p.get("exits", []):
            if x.get("to") not in w.places:
                err("exit.to", f"exit of {p['id']} goes to unknown place {x.get('to')}")
            if x.get("door") and x["door"] not in openable:
                err("exit.door", f"exit of {p['id']}: door {x['door']} is not an openable item")
            rules(f"exit {p['id']}->{x.get('to')}", x.get("when", []))
        for v in p.get("variants", []):
            rules(f"variant of {p['id']}", v.get("when", []))
    for i in w.lights:
        if isinstance(w.items[i]["light"], list):
            rules(f"light of {i}", w.items[i]["light"])
    for t in [*w.places.values(), *w.items.values(), *w.chars.values()]:
        for d in t.get("details", []):
            rules(f"detail of {t['id']}", d.get("when", []))
        for version in [t, *(t.get("states") or {}).values()]:
            for k in SITUATED:
                if isinstance(version.get(k), list):
                    for x in version[k]:
                        rules(f"{k} of {t['id']}", x.get("when", []))
                    if version[k] and version[k][-1].get("when"):
                        err("line.default", f"{t['id']}: the last of its {k} lines needs no condition (said otherwise)")
    for a in w.actions:
        where = f"action {a.get('id')}"
        if not a.get("triggers"):
            err("action.no_trigger", f"{where}: no trigger")
        for t in a.get("triggers", []):
            if w.lang.canonical_verb(t.get("verb", "")) != t.get("verb"):
                err("trigger.verb", f"{where}: verb {t.get('verb')} is not a canonical verb")
            elif t["verb"] in NEVER_SCRIPTED:
                err(
                    "trigger.builtin_verb",
                    f"{where}: verb {t['verb']} is built in (moving and taking are automatic), use another",
                )
            for f in ("object", "target"):
                if t.get(f) and t[f] not in ids:
                    err("trigger.reference", f"{where}: {f} {t[f]} is not an id")
            if {t.get("object"), t.get("target")} & set(w.buttons):
                err(
                    "trigger.button",
                    f"{where}: a code's buttons are built in (pressing them plays the code): no action on them",
                )
            if box := _builtin_twin(w, t):
                err("trigger.builtin_open", f"{where}: opening {box} is built in (freely, or with its key): no action for it, "
                              f"put what it says in the item's texts.open / texts.unlock")  # fmt: skip
        if a.get("place") and a["place"] not in w.places:
            err("action.place", f"{where}: place {a['place']} is not a place id")
        rules(where, a.get("when", []))
        check_effects(where, a.get("effects", []))
    for t in [*w.items.values(), *w.places.values()]:
        after = t.get("after")
        if after is None:
            continue
        allowed = {"enter"} if t["id"] in w.places else {"take", "open"}
        if not isinstance(after, dict) or set(after) - allowed:
            err("after.kind", f"{t['id']}: after is {{{', '.join(sorted(allowed))}: {{when, effects, text}}}}")
            continue
        for k, r in after.items():
            rules(f"{t['id']} after {k}", r.get("when", []))
            check_effects(f"{t['id']} after {k}", r.get("effects", []))
            if any(w.endings.get(e, {}).get("kind") == "lose" for e in args_of(r.get("effects"), "end")):
                err("after.loses", f"{t['id']} after {k}: a reaction never loses (no warning comes before it)")
    for r in w.riddles:
        ans = w.thing(r)["answer"]
        if not ans.get("question") or not [x for x in ans.get("accept") or [] if w.lang.stems(x)]:
            err("answer.missing", f"{r}: an answer needs a question and accepted answers (accept)")
        if r in w.items and not w.items[r].get("openable") and not ans.get("effects"):
            err("answer.on", f"{r}: a thing that asks a riddle opens (container, door) or has effects")
        for k in set(ans) - ANSWER_FIELDS:
            err("answer.field", f"{r}: answer.{k} is not one of {sorted(ANSWER_FIELDS)}")
        check_effects(f"{r} answer", ans.get("effects", []))
    for x in [i for i, it in w.items.items() if it.get("code")] + w.riddles:
        if x not in w.clue_of:
            err(
                "puzzle.clue_missing",
                f"{x}: a code or a riddle needs its puzzle in `puzzles`, with clue_in (where "
                "its answer is learnt: the drawing, the character who asks): the checker plays it once that is met",
            )
    for z in graph.get("puzzles", []):  # the generator's puzzle dependency chart (Ron Gilbert's)
        where = f"puzzle « {z.get('goal', '')} »"
        solver = z.get("action") or ""
        if solver not in action_ids and not w.items.get(solver, {}).get("code") and solver not in w.riddles:
            err(
                "puzzle.action", f"{where}: action {solver!r} is not an action id (or a thing with a code, or a riddle)"
            )
        for x in z.get("needs", []):
            if x not in ids:
                err("puzzle.needs", f"{where}: needs {x}, which is not an id")
        if z.get("clue_in") not in ids:
            err("puzzle.clue_in", f"{where}: clue_in {z.get('clue_in')!r} is not an id")
        elif not _mentions(w, z["clue_in"], [x for x in z.get("needs", []) if x in ids]):
            warn("puzzle.clue", f"{where}: nothing {z['clue_in']} says names what it needs")
    win_endings = [e for e in w.endings.values() if e.get("kind") == "win"]
    lose_endings = [e for e in w.endings.values() if e.get("kind") == "lose"]
    if len(win_endings) + len(lose_endings) != len(w.endings):
        err("ending.kind", "every ending kind must be win or lose")
    spec = profile or {}
    lo, hi = spec.get("wins", (1, len(w.endings) or 1))
    if not lo <= len(win_endings) <= hi:
        err("profile.wins", f"there must be between {lo} and {hi} win endings")
    lo, hi = spec.get("loses", (0, len(w.endings)))
    if not lo <= len(lose_endings) <= hi:
        err("profile.loses", f"{w.difficulty}: {lo} to {hi} lose endings (got {len(lose_endings)})")
    lo, hi = spec.get("places", (1, len(w.places) or 1))
    if not lo <= len(w.places) <= hi:
        err("profile.places", f"{w.difficulty}: {lo} to {hi} places (got {len(w.places)})")
    try:
        report = solve(w)
    except Exception as exc:  # a graph too broken to run: the structure errors say why
        err("play.crash", f"the adventure cannot be played: {type(exc).__name__}")
        return {}
    if not report["complete"]:
        err("play.too_big", "the adventure is too big to check (too many states): fewer objects or places")
    if not report["wins"]:
        err("play.no_win", "no win ending can be reached from the start")
    for e in win_endings:
        if e["id"] not in report["wins"]:
            err("play.win_unreachable", f"win ending {e['id']} can never be reached" + _why_not(w, e["id"], report))
    for e in lose_endings:
        if report["complete"] and e["id"] not in report["loses"]:
            err("play.lose_unreachable", f"lose ending {e['id']} can never be reached" + _why_not(w, e["id"], report))
    if report.get("stuck"):
        path = ", ".join(":".join(s) for s in report["stuck"])
        err("play.stuck", f"the hero can get stuck (no way to win any more) after: {path}")
    for wrong in disagreements(w) if report["complete"] else []:
        err("play.disagree", f"the game does not do what the checker counts on: {wrong}")
    if report.get("path"):
        report["chart"] = chart = derive(w, report["path"])
        if chart["width"] == 1 and len(chart["puzzles"]) >= 5:
            warn(
                "chart.corridor",
                f"the {len(chart['puzzles'])} puzzles of the shortest win form one corridor (each "
                "needs the one before): give the child two puzzles to work on at once somewhere",
            )
        steps = useful_steps(w, report["path"])
        report["shortest"] = len(steps)  # the things picked up on the way that serve nothing don't count
        codes = {w.buttons[i] for k, i in steps if k == "press"}  # a code is one puzzle, whatever its length
        report["puzzles"] = sum(1 for k, _ in steps if k in ("act", "open", "answer")) + len(codes)
        lo, hi = spec.get("steps", (0, report["shortest"]))
        if not lo <= report["shortest"] <= hi:
            err(
                "profile.steps",
                f"{w.difficulty}: the shortest win must take {lo} to {hi} steps (got {report['shortest']})",
            )
        lo, hi = spec.get("puzzles", (0, report["puzzles"]))
        if not lo <= report["puzzles"] <= hi:
            err(
                "profile.puzzles",
                f"{w.difficulty}: the shortest win must solve {lo} to {hi} puzzles (got {report['puzzles']})",
            )
    for a in w.actions:  # a clue by design (« flag:jamais »), or a typo that made a step a clue?
        for f in sorted(args_of(a.get("when"), "flag") - w.settable):
            if a.get("effects") and (near := difflib.get_close_matches(f, sorted(w.settable), n=1, cutoff=0.75)):
                warn("flag.typo", f"action {a['id']} needs flag {f}, which nothing sets: did you mean {near[0]}?")
    dead = [a["id"] for a in w.actions if not w.red_herring(a) and a["id"] not in report["fired"]]
    for a in dead if report["complete"] else []:
        warn("action.dead", f"action {a} can never happen")
    for text in _prose(w):
        warn("text.lies", text)
    for who, said in _persona_clues(w):
        warn("persona.clue", f"the persona of {who} says {said}: the narrator plays it at every turn, a clue "
             "the puzzles don't give (keep the voice, the manners, the mood)")  # fmt: skip
    return report


def _persona_clues(w: World) -> list[tuple[str, str]]:
    """A persona that tells an answer or names a thing a puzzle needs which the character's own lines
    never name: the narrator would give it away while improvising."""
    stems = w.lang.stems
    answers = {a: stems(a) for r in w.riddles for a in w.thing(r)["answer"].get("accept", []) if isinstance(a, str)}
    needs = {x for z in w.g.get("puzzles", []) if isinstance(z, dict) for x in z.get("needs") or [] if x in w.items}
    public = stems(" ".join(str(w.g.get(k) or "") for k in ("title", "intro", "goal")))
    out = []
    for c, ch in w.chars.items():
        persona = ch.get("persona")
        if not isinstance(persona, str) or not persona:
            continue
        words = stems(persona)
        lines = {k: v for k, v in ch.items() if k not in ("persona", "answer")}  # what they say, not the answer
        own = stems(" ".join(_texts({**lines, "question": (ch.get("answer") or {}).get("question", "")})))
        said = [f"« {a} »" for a, s in answers.items() if s and s <= words and not s <= own]
        for x in sorted(needs - {c}):  # not the story's own subject (the title, the intro, the goal)
            labels = [s for label in w.names.labels(x) if (s := stems(label))]
            if any(s <= words for s in labels) and not any(s <= own or s <= public for s in labels):
                said.append(w.name(x))
        if said:
            out.append((c, ", ".join(said)))
    return out


def _prose(w: World) -> list[str]:
    """Texts that will lie (the narrator believes them): a place's lasting text naming someone who
    leaves (it belongs in their `initial`), a lock told where it may be open or the key in hand."""
    out = []
    movers = {c for a in w.actions for c in args_of(a.get("effects"), "move", "follow")}
    movers |= {c for r in w.riddles for c in args_of(w.thing(r)["answer"].get("effects"), "move", "follow")}
    for p in w.places.values():
        if p.get("variants"):  # version 1: whole descriptions replaced when things change
            continue
        lasting = [p.get("description", "")] + [d.get("text", "") for d in p.get("details", []) if not d.get("when")]
        words = w.lang.stems(" ".join(lasting))
        for c in sorted(movers & set(w.chars)):
            if any(
                w.lang.stems(x) and w.lang.stems(x) <= words
                for x in (w.chars[c].get("name", ""), *w.chars[c].get("aliases", []))
            ):
                out.append(f"place {p['id']}: its description names {c}, who moves: say it in {c}'s initial")
        locks = [i for i, it in w.items.items() if it.get("locked")]
        told = [d for d in p.get("details", []) if d.get("when") and _locked(w, d.get("text", ""))]
        unguarded = [t for t in lasting + [d.get("else", "") for d in p.get("details", [])] if _locked(w, t)]
        if locks and (unguarded or any(not args_of(d["when"], "has", negated=True) for d in told)):
            out.append(f"place {p['id']}: a text says « fermé à clé » even when the hero holds the key: "
                           "put it in a detail with when [!open:X, !has:KEY]")  # fmt: skip
    return out


def _locked(w: World, text: str) -> bool:
    """Does a text say « locked » (in the world's language)?"""
    return bool(w.lang.locked_words and w.lang.locked_words.search(text))


def _texts(node) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for k, v in node.items() if k not in ("id", "at", "when", "key", "kind") for t in _texts(v)]
    if isinstance(node, list):
        return [t for v in node for t in _texts(v)]
    return []


def _mentions(w: World, ident: str, needs: list[str]) -> bool:
    """Does something the thing or place says or shows name one of the things needed (a clue)?"""
    if not needs:
        return True
    words = w.lang.stems(" ".join(_texts(w.thing(ident) or w.places.get(ident, {}))))
    return any(w.lang.stems(label) and w.lang.stems(label) <= words for x in needs for label in w._labels(x))


def _why_not(w: World, ending: str, report: dict) -> str:
    """Why an ending is never reached: the first condition of its actions that nothing makes true, or
    the action the hero never gets to do."""
    for a in w.actions:
        if f"end:{ending}" not in a.get("effects", []):
            continue
        for r in a.get("when", []):
            if _never_true(w, r):
                return f": action {a['id']} needs {r}, and no effect ever makes it true"
        missing = [x for t in a.get("triggers", []) for x in (t.get("object"), t.get("target"))
                   if x and _never_true(w, f"has:{x}") and x in w.items and not w.items[x].get("at")]  # fmt: skip
        if missing:
            return f": action {a['id']} uses {missing[0]}, which is never in the world (reveal it or place it)"
        if a["id"] not in report.get("fired", ()):
            return f": action {a['id']} can never be done (its conditions never hold together where its things are)"
    return ""
