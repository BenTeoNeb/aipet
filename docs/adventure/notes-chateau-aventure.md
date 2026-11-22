# Notes: Château Aventure (IELLO, 2018), as a model for the « aventure » activity

Sources read (2026-11-21): `CHATEAU-AVENTURE_rules_FR_light.pdf` (rules, 12 p.),
`CHATEAU-AVENTURE_scenario-chateau-aventure_light.pdf` (scenario « Château Aventure », J. A. Sorensen),
`scenario_Port_au_singe.pdf` (scenario « Port aux Singes », L. Papaïs). These are structural notes;
the scenarios are copyrighted and are not to be copied into the pet (inspiration only).

## 1. The principle

- An « IRL » version of the 1980s text adventures (Zork-like). One player plays **the computer**;
  a map + a scenario sheet replace the software.
- All the other players play **one single character**, **in turns**: each gives one command, the
  computer answers, the turn passes on *whether the command was valid or not*.
- Goal: solve the scenario (win condition). « Continuez jusqu'à ce que le héros gagne ou que tout le
  monde soit frustré et rentre chez soi… »
- Spirit: « comme un karaoké » — the aim is to amuse more than to play right. The computer leads,
  must « amuser et stimuler ».

## 2. The computer's job (game master)

- Opening: announce the title (« Bienvenue à… Château Aventure ! [pause] »), then the start: where
  the character is, **what it sees, every object in the zone, the exits**. Later the description can
  be shortened once a place is familiar.
- Keep in memory: **location, inventory, score** (+ the state of the world: who is knocked out, which
  door is open…). « Le plus dur est de garder une trace de ce qui se passe. »
- On arriving in a new place: description, notable elements, exits.
- Speaks as « je » for the computer itself (« Je ne sais pas comment lancer quelque chose » = the
  software does not know), « vous » to the character.

## 3. Commands

- Spoken, **one or two action words**, `<verbe> <nom>`: « Aller ouest », « Monter arbre »,
  « Examiner garde ». Sometimes 3 parts: « Donner poisson au troll », « Utiliser canne à pêche sur
  étang », « Parler à princesse de couronne ».
- Families:
  - **Movement**: Aller, Sortir, Quitter, Entrer, Est, Ouest, Nord, Sud, Haut, Bas (« Ouest » alone
    is fine; « Sortir » when there is one exit).
  - **Senses**: Regarder (repeat the place: description, content, exits), Examiner (a person or
    object), Chercher / Fouiller.
  - **Interaction**: Prendre, Laisser, Parler, Tuer, Éclairer, Allumer, Attendre, Porter, Utiliser,
    Donner, Combiner, Cueillir, Sentir, Lire, Acheter, Demander…
  - **Meta** (always valid): Menu, Aide (optional, to unblock), Inventaire, Sauvegarder / Charger
    (3 saves per game, remind how many remain), Score (0–100), Quitter.
- Answers to non-scripted commands: **improvise**; if absurd: « ce n'est pas possible » or
  « Commande inconnue ! ». Examples of the expected register:
  - « Manger canne à pêche » → « Vous ne pouvez pas manger ça ! »
  - « Aller poisson » → « Vous ne pouvez pas aller par là. »
  - « Prendre voiture » → « Il n'y a pas de voiture ici. »
  - « Utiliser clef » → « L'utiliser sur quoi ? » (a question, not a refusal)
  - « Monter très grand arbre » → « Je ne comprends pas la commande. » (the scripted noun was
    « très grand z'arbre »: **strictness is part of the joke** in the original).
- The rules explicitly ask to be strict: « N'autorisez pas les joueurs à négliger la grammaire, à
  donner des commandes complexes ou à faire des blagues. Répondez à la façon d'un ordinateur (mais
  un ordinateur doté d'un sens de l'humour). » → **this is what we do NOT want** (see design).

## 4. Design rules of the puzzles (worth keeping)

- « Soyez cruel, mais juste. » Puzzles « agaçants mais logiques », the computer « stupide mais fiable ».
- **Always winnable**: « jouer au jeu correctement doit toujours permettre aux joueurs d'arriver à
  cette fin. Il ne peut pas y avoir de configuration où le personnage est coincé. » (no dead end)
- **Hint on wrong timing**: a command given at the wrong moment gets a clue (« Épouser princesse » →
  « La princesse n'épousera qu'un roi. » — now the players know the next sub-goal).
- **NPCs carry the hints** (the princess « Tu portes déjà de quoi allumer » when they forget the
  inventory; the guards « font des vocalises » in Port aux Singes).
- **Consistency**: every action has a reaction, the same command gives the same answer.
- **Humour** comes from the gap between the character's expectations and the outcome, from voices
  and sound effects (the slap, the falsetto princess, the « z'arbre » liaison bug), never from
  winking at the audience.
- Players should not have time to confer (one command per turn), but the computer may allow it.
- Players may take notes if the group enjoys it.
- Rules are adaptable (« pas gravées dans le marbre »), e.g. play without score.

## 5. Losing, saving, score

- Losing = **death** (monster, trap, absurd command like « Tuer moi », « Sauter falaise ») → « Vous êtes
  mort. Merci d'avoir joué ! Votre score est de 15/100. » then load the save or restart.
- Saves: 3 per game; loading keeps the score at the save, restarting resets it.
- Score (0–100, mostly for nostalgia/comedy). Château Aventure: 2 pts per place visited (max 24),
  5 per new object (max 35), 10 per enemy defeated (max 30), 5 if the rose is given to the princess
  before anything else, 5 for marrying her, 1 for finishing without dying. Winning ≈ 100.
- **Timers / pressure**: the troll kills a character who stays too long; the ghost « s'approche petit
  à petit ». Some actions annoy a guardian → death.

## 6. Anatomy of a scenario sheet

- Intro page (computer only): starting inventory (« Torche allumée »), number of saves, GM advice,
  score table, special rules, difficulty pictogram (3 levels), the opening line to read aloud.
- One block per **place**: name; description to read (in quotes) — *conditional variants* (« Si le
  garde est assommé : … »); **exits** (some « bloquée » with the unlocking condition); **scripted
  actions** in the right column (`Commande` → answer, often with `• Si …` / `• Sinon …` branches);
  **place rules** (NPC behaviour, timers, who follows the character).
- Map page: places as boxes linked by exits; icons for: interactive object, object to collect,
  interactive character, result of an action.
- Colour code: text to read aloud vs text for the GM only.

## 7. Scenario « Château Aventure » as a graph

Places (11): Hutte → Jardins royaux (rosier) → Étang (E) / Sentier sinueux (O) → Cime du très grand
z'arbre (monter) / Pont-Levis (N, troll) → Cour du château (garde) → Escalier de la Tour (monter,
porte fermée) → Tour (princesse) ; Cour → Escalier des Catacombes (descendre, trop noir) → Catacombes
(fantôme à couronne) ; Cour → Grande Salle de Banquet (N, bloquée par le garde ; bougie à runes) →
Salle du Trône (trône d'or).

Start: Hutte, inventory = torche allumée, 3 saves.

Dependency chain (lock → key):

```
canne à pêche ─use@étang→ poisson ─give→ troll ⇒ exit Pont-Levis→Cour open
branche morte (z'arbre; « grimper branche » = death, « prendre branche » = ok)
   ─hit→ garde (branche cassée) ⇒ garde assommé ⇒ exit Cour→Banquet open, « prendre clef » ok
clef ─open→ porte de la tour ⇒ Tour
rose (jardins) ─give→ princesse ⇒ she talks (bonus if first interaction)
torche ─use@escalier catacombes⇒ catacombes reachable (shortcut cour↔catacombes)
bougie (banquet) + torche ─light@catacombes→ fantôme fuit ⇒ couronne au sol ⇒ prendre couronne
couronne ─give→ princesse ⇒ couronne portée ⇒ garde s'agenouille, banquet en fête, mariage possible
couronne portée ─s'asseoir@trône⇒ WIN (« Longue vie au roi… »)
```

Observations:
- ~9 locks for 11 places; items are mostly **single-use keys**; the branch is *consumed* (breaks).
- Red herrings: « Porter couronne » (« vous n'oseriez pas » — must be *given* to the princess), the
  sword (« trop peur de vous blesser »), « manger poisson » (cru), « nager étang » (trop froide).
- Conditional descriptions (the courtyard changes with the guard's state and the crown).
- Deaths: jump/climb the branch, attack the troll, stay near the troll, take the key while the guard
  is awake, touch the ghost or stay too long.

## 8. Scenario « Port aux Singes » as a graph (shorter, 6 places — a good size for us)

Places: Quartier du capitaine ↔ Bateau (le pont, le mousse) ↔ Ponton (le second) ↔ Place de la
ville (colporteur, affiche) ↔ Auberge (aubergiste, Maria Carabas + son agent) / Palais du Gouverneur
(gardes, carte au trésor) / Jardins privés (molosse, fleurs).

Start inventory: empty (« Vide »), 3 saves. The objective is **discovered in play** (read the sheet:
« voler la carte aux trésors »; the second explains the stakes: mutiny).

Chain:

```
fouiller lit → 2 doublons            lire feuille → objectif + ruban
mousse (fatigué) + doublon → somnifère   (or: mousse follows you, buys it at the colporteur)
aubergiste + doublon → cuisse de poulet
cuisse + somnifère ─combine→ cuisse empoisonnée ─give→ molosse ⇒ endormi
cueillir fleurs (jardins, molosse endormi) ; fleurs + ruban ─attach→ bouquet
bouquet → Maria (the agent lets you talk) ─« demander de chanter »→ everyone goes to listen,
   the guards too ⇒ palais unguarded ⇒ prendre carte = WIN
```

Observations:
- **Scarce resource**: exactly 2 doublons for 2 purchases — spending one elsewhere could block the
  game (a weakness against the « jamais coincé » rule, avoided only because nothing else is for sale).
- **Combination** of inventory items (cuisse + somnifère, fleurs + ruban) is the heart of the puzzles.
- **Followers**: the mousse can follow you; then describe him in every place.
- Talking NPCs carry the story (objective, stakes, hints: the tired mousse → somnifère).
- Near-miss answers that teach: « acheter potion somnifère » → « pas besoin, vous ne vous sentez pas
  fatigué » (points to the mousse); fleurs alone → « Maria mérite quelque chose de plus distingué »
  (points to the bouquet).
- Anything not scripted → « Commande inconnue » (sheet intro). Again: what we replace.

## 9. What to keep / change for children aged ~4–8, by voice

Keep: one shared hero played in turns; places / objects / exits announced on arrival; a world state
that is reliable (code, not memory of the model); lock-and-key puzzles that are logical and always
winnable; hints through NPCs and wrong-timing answers; humour, voices, sound effects; Regarder /
Inventaire; a goal discovered through the story.

Change:
- **No « Commande inconnue », no strict grammar**: the children speak naturally (« je veux donner le
  poisson au gros monsieur vert »); the narrator understands intent, accepts synonyms, partial names,
  several actions in one sentence, and answers *in the story*.
- **Imagination has room**: an unscripted but harmless action gets a short invented outcome that
  changes nothing important (and is remembered as decor), instead of a refusal.
- **Impossible stays impossible**, said in-world and kindly, ideally with a nudge.
- **No death** for little ones: mishaps (« Plouf ! tu es tout mouillé, tu remontes sur la rive »)
  and at worst a step back; « perdre » = running out of time/turns (the tide, the sunset) with
  « on réessaiera ». Saves become automatic checkpoints.
- Cardinal directions are hard at 5: name exits by landmarks (« vers l'étang »), accept both.
- Shorter: 5–7 places, 3–4 locks, 15–20 minutes, generated for the child, reusing the story
  universe's characters (Piko, Raya…).
- Score optional, kid-friendly (stars / treasures found), never compared between children.
