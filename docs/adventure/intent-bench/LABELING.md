# Intent benchmark: writing and labelling a child's sentences

We measure whether a fast "decision" model can turn what a child (5–8 years old, French, speaking
aloud, through speech recognition) says during a voice adventure game into the right engine
actions. You define the truth (`gold`): the actions a careful game master would send to the
engine for this sentence, in this situation.

Tools (run from the repository root, with `uv run python ...`):
- `B=docs/adventure/intent-bench`
- `uv run python $B/show.py FILE.json [from] [to]`: each case's context (place, what is in reach
  with ids, the bag, the places already seen, what the narrator just said, the child's sentence).
- `uv run python $B/validate.py FILE.json`: plays your gold actions on each case's state and lists
  what looks wrong. Run it on your output and fix every listed case (or keep it if you are sure,
  e.g. the child really tries something not in reach: then note it in `why`).
- You may read the adventure itself: `aipet/data/adventures/*.json` (or
  `docs/adventure/model/le-phare-de-piko.json` for « Le phare de Piko (nouvelle version) »).

## The gold label

```json
"gold": {"kind": "act", "actions": [{"verb": "give", "object": "algue", "target": "pinces"}], "why": "…"}
```

- `kind`:
  - `act`: the sentence asks for one or more game actions (even ones that will fail or are pure
    imagination: « je saute », « je chante une chanson au crabe »).
  - `hors_jeu`: not a game action: chit-chat with the pet, a question about the real world, « j'ai
    faim », « c'est quoi ton jeu préféré ».
  - `preciser`: so ambiguous that a good game master would ask before acting (« je prends ça »
    with three things around and nothing pointed at before; a garbled sentence with two equally
    likely readings). Use it sparingly: most clumsy sentences have one clear reading in context.
- `actions` (for `act`): in the order said. Each: `verb` (one of the list below), and only the
  fields that apply, by **id** (the keys of `à_portée`, `sac`, `lieux_connus`, or `lieu_id`):
  - `object`: the thing taken, given, used, looked at, opened; the person talked to or touched.
  - `target`: who or what it is given to, used on, thrown at.
  - `to`: for `go`, the place id (a neighbour from « → » entries, or any place in `lieux_connus`:
    a far place is walked to automatically).
  - `answer`: `object` = the child's words (the answer itself, e.g. "le phare"), `target` = the id
    of who or what asked the riddle.
  - a code (« rose, blanc, bleu »): one `push` per button, in the order said.
  - a thing that is not in the adventure (« mon doudou », « un caillou » when no pebble exists):
    leave `object` out and keep the verb (the engine treats it as imagination).
  - `improvise`: never (it needs the game master's private screen; label the plain actions).
- `why`: one short line when the reading is not obvious (pronoun resolved, speech-recognition error
  fixed, far place, etc.).

Verbs: go, take, drop, give, use, combine, open, close, talk, ask, answer, look, examine, search,
inventory, push, pull, light, throw, hit, destroy, eat, drink, climb, swim, jump, touch, listen,
smell, fill, read, wear, where, help, wait, sit, sing, dance, other.
- talk = parler à, saluer ; ask = demander quelque chose à quelqu'un ; look = regarder autour ;
  examine = regarder une chose de près ; search = fouiller/chercher ici ; where = « où est… ? » ;
  help = demander de l'aide/un indice ; inventory = « qu'est-ce que j'ai dans mon sac ? » ;
  climb = monter dans/sur une chose (« monter dans la barque ») ; going somewhere is go ;
  destroy = casser/effacer/détruire pour de bon ; use = utiliser/mettre/peindre/frotter X (sur Y).
- Use the engine's conventions as above; when two verbs are equally right (open the door with the
  key = `open door` or `use key on door`), pick either: scoring compares the engine's results.

## Writing sentences (when asked to)

Write what a real child of 5–8 says aloud in French, as speech recognition would transcribe it:
short, spoken, sometimes clumsy (« euh », « le truc là »), sometimes with a transcription slip
(« la barre » for « la barque »), sometimes several actions at once. For each situation, write 5
different sentences, varied across the batch:
- a simple action on something in reach;
- several actions in one sentence (2–3);
- a pronoun or reference to what the narrator just said (« je lui donne », « j'y vais »), or a far
  place already seen (« on retourne à la cabane »);
- a hard one: a riddle answer if a riddle is here (`devinettes_ici`), a code, a creative idea, an
  imaginary object, a word mangled by speech recognition, a verb used loosely (« je fais manger la
  pomme au cheval »);
- about one in four situations: a `hors_jeu` sentence; about one in eight: a `preciser` one.
Also set `before` to a plausible previous sentence of the child when your sentence refers to it.
Don't copy the narrator's or the adventure's exact wording: children say it their own way.
