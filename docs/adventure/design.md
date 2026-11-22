# Design proposal: the « aventure » activity

Status: phases 1 and 2 implemented (2026-11-21): `aipet/games/adventure_engine.py`, `aipet/games/adventure.py`,
`aipet/builtin_skills/adventure/SKILL.md`, `aipet/data/adventures/`. Background notes: `notes-chateau-aventure.md`.

A text adventure in the spirit of Château Aventure, told by the pet: the children play one hero in
turns, explore places, pick up and combine objects, talk to characters, solve locks, and win (or
not). The world is **generated as a graph** and **run by code**; the model is the narrator, never
the source of truth.

## 1. Principle: the code is the world, the model is the voice

```
child speaks ──▶ conversation model (narrator)
                   │  interprets intent, generously
                   ▼
              adventure_act(intents) ──▶ engine (code): graph + state
                   ▲                       resolves names, checks conditions,
                   │                       applies effects, picks hints
                   └── result: what happened (facts) + what to tell + how
narrator ──▶ tells it as a story (voice, humour, sound effects), « Et maintenant, que fais-tu ? »
```

- The engine decides **what is true** (where we are, what we hold, what is open, did we win).
- The narrator decides **how it sounds**, and may invent **colour that changes nothing**.
- The engine never answers « commande inconnue »: every intent gets one of a few outcome kinds
  (§4) that the narrator turns into story.

### Who does what (decided 21/11/2026: no overfitting of the engine)

- **The engine** states facts only: what exists here, what happens, what is impossible, what changed. It
  keeps a small safety net (verb synonyms, fuzzy names) but no rule that guesses the child's intent.
- **The narrator** (the conversation model) understands the child: it picks the verb and the words of the
  world (« monter dans la barque » → climb), asks a clarifying question when an action is unclear or very
  odd, plays along with harmless unplanned actions and comes back to the situation (« Maintenant que c'est
  fini, que fais-tu ? »), and chooses the adventure in the bank from the child's idea (catalogue in its
  instructions, `title` in `adventure_start`; word matching is only a fallback).
- **When the child goes round in circles**, the engine first sends a reminder of what can be done (ways,
  things, characters, bag, a few ideas — never which one wins), then hints of growing strength.

## 2. The adventure graph (JSON, generated then validated)

```jsonc
{
  "title": "Le phare de Piko", "intro": "…", "goal": {"text": "rallumer le phare", "when": [{"flag": "phare_allume"}]},
  "start": {"place": "plage", "inventory": ["lanterne"]},
  "places": [
    {"id": "plage", "name": "la plage", "aliases": ["sable", "bord de mer"],
     "description": "…", "variants": [{"when": [{"flag": "maree_haute"}], "description": "…"}],
     "exits": [
       {"to": "rochers", "label": "vers les rochers", "dir": "nord"},
       {"to": "phare", "label": "la porte du phare", "when": [{"flag": "porte_ouverte"}],
        "blocked": "La porte est fermée par un gros cadenas rouillé."}]}
  ],
  "items": [
    {"id": "cle", "name": "une petite clé dorée", "aliases": ["clé", "clef"], "at": "crabe",
     "takeable": true, "description": "…", "hidden": true}
  ],
  "characters": [
    {"id": "crabe", "name": "Pinces le crabe", "at": "rochers", "personality": "grognon mais gourmand",
     "says": "Laisse-moi tranquille, j'ai faim !", "hint": "Il regarde les algues avec envie."}
  ],
  "actions": [
    {"id": "nourrir_crabe", "verb": "give", "object": "algue", "target": "crabe",
     "when": [{"has": "algue"}], "effects": [{"lose": "algue"}, {"reveal": "cle"}, {"flag": "crabe_content"}],
     "text": "Le crabe croque l'algue et laisse tomber une petite clé.", "points": 10},
    {"id": "ouvrir_porte", "verb": "open", "object": "porte", "place": "plage",
     "when": [{"has": "cle"}], "effects": [{"flag": "porte_ouverte"}], "text": "…",
     "not_yet": "Le cadenas est bien fermé… il faudrait une clé."}
  ],
  "mishaps": [{"verb": "swim", "place": "plage", "text": "Brrr, l'eau est glacée ! Tu ressors vite.", "back_to": "plage"}],
  "red_herrings": [{"verb": "eat", "object": "algue", "text": "Beurk, c'est salé ! Le crabe, lui, adorerait."}]
}
```

- **Nodes**: places, items, characters. **Edges**: exits (with conditions) and actions (with
  preconditions and effects). Conditions are a tiny closed language: `has`, `flag`, `at`, `not`,
  `visited`. Effects: `gain`, `lose`, `flag`, `unflag`, `reveal`, `move` (a character, the hero),
  `follow` (a companion, like the mousse), `win`.
- Verbs are a **closed set of canonical verbs** (go, look, examine, search, take, drop, give, use,
  combine, open, close, talk, ask, eat, climb, swim, push, pull, light, wear, wait, …) — the
  generator must use them; the narrator maps the child's words onto them (§3).
- Conditional descriptions (`variants`) cover « Si le garde est assommé… ».

### Generation (preparation model, like the story plan)

1. Input: the child's idea (or surprise), age/class, recurring characters of the story universe
   (Piko, Raya… become NPCs), past adventures (no repeats), evening or not.
2. The model designs **backwards from the goal** (prompt: goal → its lock → the key's lock → …), 3 to
   4 locks, 5 to 7 places, 4 to 7 items, 2 to 3 characters, at least 2 red herrings, at least one
   combination (A + B), one helpful character who gives the hints.
3. **Validation by code** (§5). Errors are sent back once for repair; on a second failure, fall back
   to a hand-written adventure from a small built-in library (also used by the tests).

## 3. Permissive understanding (no « commande inconnue »)

Two layers, so that the children can talk like children:

1. **The narrator model** turns free speech into one or more intents:
   `adventure_act({"intents": [{"verb": "give", "object": "le poisson", "target": "le gros monsieur vert"}], "said": "<the child's words>"})`.
   Several actions in one sentence (« je prends la canne et je vais à l'étang ») → a list, executed in
   order, stopping at the first that fails. Unclear target → it asks back in the story (« Le donner à
   qui ? Au troll ? »), like « L'utiliser sur quoi ? ».
2. **The engine resolves names generously**: aliases from the graph, accent/article-insensitive
   match, partial words (« la canne »), descriptive words against names and descriptions (« le gros
   monsieur vert » → the troll whose description says big and green), STT slips (fuzzy match, plus
   the adventure's names sent to speech recognition like the story names). It only considers what
   is **in scope** (here + inventory + followers); an object elsewhere is « pas ici », an unknown one
   « tu ne vois pas de voiture ici ».
3. **Verb synonyms** live in code (French lists: prendre/attraper/ramasser/cueillir → take; donner/
   offrir/lancer à → give; utiliser/se servir de/mettre … sur → use…), but the narrator already
   normalises, so this is a safety net. « use » on an object also tries the scripted action whose
   object matches (« utiliser canne à pêche » at the pond = fish).

## 4. Outcomes the engine returns (and how the narrator tells them)

| outcome | when | narrator |
|---|---|---|
| `done` | a scripted action / move / take succeeded | tells `text` in its own words, then the new place (things + exits) if the hero moved |
| `not_yet` | the right action at the wrong moment (missing item, NPC not ready) | in-world reason + the action's `not_yet` hint (« La princesse n'épousera qu'un roi ») |
| `impossible` | against the world (swim in lava, take the castle, go where no exit leads) | kind in-world no (« Le troll est bien trop lourd à soulever ! »), suggest what is around |
| `mishap` | a scripted danger | small comic mishap, step back to a safe place — **never death** |
| `free` | harmless, not scripted (dance with the troll, sing to the fish, name the boat) | **imagination**: invent a short, fun outcome that **changes nothing in the game** (no item, no open door, no solution); the engine stores it as decor (`colour` notes per place/character) so it stays consistent later (« le poisson que tu as appelé Bubulle ») |
| `ask` | ambiguous (two keys, « utiliser » without target) | a question in the story |
| `won` / `over` | goal reached / time ran out or children stop | the ending; for `over`, « on reprendra plus tard » |

The result always includes the facts the narrator needs (current place, visible things, exits,
inventory changes) so it never has to remember them; the system prompt also carries a compact
« Aventure en cours » section (place, inventory, known goal, decor notes), refreshed after each act.

**Hints, by code**: the validator computes the solution path; after N turns without progress
(configurable, lower for young children) the engine attaches a hint for the next step on the
shortest path, in three levels: where to look → which object → almost the answer. The narrator gives
it through a character (the mousse yawns, the princess remarks…), never as « l'ordinateur ».

## 5. Validation: always winnable, never stuck

The state space is small (place × inventory × flags), so the engine can explore it:

- every id referenced exists; every place reachable; the goal is reachable from the start;
- **no dead end**: from *every* reachable state the goal is still reachable (catches consumed keys,
  spent coins like the doublons of Port aux Singes); if one exists, repair (make the item reusable)
  or reject;
- size limits (places, locks, solution length) by age; the shortest solution length is the
  difficulty; red herrings exist and do not block anything.

## 6. State, memory, multi-children

- State per adventure: place, inventory, flags, visited places, decor notes, turns, hints given,
  stars, followers, checkpoint. Saved after every act in the family memory
  (`histoires/aventures/<id>.json`, one file per adventure) → an unfinished adventure is resumed
  next time (« On reprend : tu étais devant le pont-levis avec un poisson dans ton sac »).
- On end: outcome (won / stopped / time out), turns, hints used, favourite moment → the child's
  card and the story universe (NPCs met become recurring characters; the treasure becomes a trophy
  Flocon can talk about).
- **No turn management**: Flocon is the game master and narrator, whoever speaks plays the hero.
- **Losing is possible at every difficulty** (lose endings, never death or fear); a lost adventure can be
  retried from just before the mistake (« sauvegardes »: unlimited in facile, 3 in moyen, 1 in
  difficile) or from the start. Every adventure is stored, with all its runs, and can be replayed.
- Score: stars for treasures found and helping characters (no number out of 100 for 5-year-olds;
  « Score » stays a fun meta command).

## 7. Tools (as for the story)

- `adventure_start(idea, resume)` — prepares (generation + validation, with the waiting phrase),
  returns the intro + first place.
- `adventure_act(intents, said)` — the turn (§3–4).
- `adventure_end(reaction)` — records the end, suggests something else (variety).
- Skill `builtin_skills/adventure/SKILL.md`: the narrator's rules — describe arrivals (things and
  exits, by landmarks), call `adventure_act` for every in-game sentence, never say « commande
  inconnue », impossible = in-world, invent only harmless colour, keep it short for the voice, voices
  and sound effects, whose turn it is, never reveal the solution, hints only from the engine.

## 8. Phases

1. **Engine** (graph model, conditions/effects, name resolution, outcomes, state save/resume) +
   **validator/solver** + 2 hand-written original adventures + tools + skill + tests that play
   through them (wins, not_yet, free, mishap, resume). Playable right away.
2. **Generation** from the child's idea + repair loop + fallback; adventure names to speech
   recognition; variety (`label: une aventure dont tu es le héros`).
3. **Hints ladder**, turn-taking between children, time pressure, trophies, parents' page (map of
   the graph and progress).

## 9. Decisions (2026-11-21)

- Losing is possible for every age; every adventure is stored and can be retried.
- Directions: both landmarks (« vers l'étang ») and cardinal points (« à l'est ») work.
- No turn-taking between children: one game master, one hero, whoever speaks.
- 1 to 3 ways to win per adventure (checked: each win ending must be reachable).
- Three difficulties (facile, moyen, difficile), **inherent to the adventure**: chosen when it is created,
  stored with it, kept when it is replayed. They set the size (places, length of the shortest win), the
  number of ways to lose, how soon hints come and how many « sauvegardes » there are
  (`DIFFICULTIES` in adventure_engine.py).
