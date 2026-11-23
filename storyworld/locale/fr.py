"""French: the words children say for each verb, how names are normalized, and the notes the engine
joins to its answers for a French-speaking narrator. Another language provides the same names."""

from __future__ import annotations

import re
import unicodedata

from storyworld.locale import Language, cap


def normalize_text(s: str) -> str:
    """Lower case, without accents."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


# ---- verbs --------------------------------------------------------------------------------------
# Canonical verbs (what the narrator sends and the generator writes) and the French words for them:
# a safety net, the narrator already maps the child's words.
VERBS: dict[str, list[str]] = {
    "go": [
        "va",
        "vais",
        "vas",
        "allons",
        "allez",
        "viens",
        "aller",
        "partir",
        "sortir",
        "entrer",
        "monter",
        "descendre",
        "marcher",
        "courir",
        "retourner",
    ],
    "look": ["regarder", "observer", "decrire"],
    "inventory": ["inventaire", "sac", "poches"],
    "examine": ["examiner", "inspecter", "voir", "regarder de pres"],
    "search": ["fouiller", "chercher", "explorer", "creuser", "gratter"],
    "take": ["prendre", "attraper", "ramasser", "cueillir", "recuperer", "emporter", "voler"],
    "drop": ["poser", "laisser", "lacher", "jeter par terre"],
    "give": ["donner", "offrir", "tendre", "nourrir", "montrer", "montre"],
    "use": [
        "peins",
        "peint",
        "ecris",
        "ecrit",
        "mets",
        "met",
        "mettre",
        "souffler",
        "jouer de",
        "peindre",
        "dessiner",
        "colorier",
        "ecrire",
        "tracer",
        "coudre",
        "planter",
        "brancher",
        "reparer",
        "utiliser",
        "se servir",
        "frotter",
        "frotte",
        "essayer",
    ],
    "combine": ["combiner", "melanger", "assembler", "attacher", "accrocher", "coller", "tremper"],
    "open": ["ouvrir", "deverrouiller", "deverrouille", "deplier", "derouler"],
    "close": ["fermer"],
    "talk": ["parler", "dire", "discuter", "appeler", "saluer", "reveiller"],
    "answer": ["repondre", "reponds", "repond", "deviner", "devine"],
    "ask": ["demander"],
    "eat": ["manger", "croquer", "gouter"],
    "drink": ["boire", "bois", "boit"],
    "climb": ["grimper", "escalader"],
    "swim": ["nager", "plonger"],
    "jump": ["sauter"],
    "push": ["appuie", "appuies", "pousser", "appuyer", "tourner", "presser", "secouer", "secoue"],
    "pull": ["tirer", "tire", "tires"],
    "light": ["allumer", "eclairer", "enflammer"],
    "wear": ["porter", "enfiler", "coiffer"],
    "read": ["lire", "lis", "dechiffrer"],
    "hit": ["tape", "frapper", "taper", "attaquer", "combattre", "assommer"],
    # Making a thing gone for good (« effacer le dessin », « jeter la clé à la mer »): allowed when it is
    # plausible; if the adventure can't be won any more, it ends when the child runs into it.
    "destroy": [
        "effacer",
        "efface",
        "detruire",
        "detruis",
        "casser",
        "casse",
        "briser",
        "jeter a la mer",
        "jeter dans la mer",
        "jeter a l'eau",
        "jeter dans l'eau",
    ],
    "where": ["ou est", "ou sont", "ou se trouve", "ou il est", "ou elle est"],
    "smell": ["sentir", "renifler"],
    "touch": ["toucher", "caresser", "chatouiller", "gratouiller", "calin", "tater", "palper"],
    "listen": ["ecouter"],
    "wait": ["attendre"],
    "sit": ["s'asseoir", "asseoir"],
    "sing": ["chanter"],
    "dance": ["danser"],
    "throw": ["lancer", "jeter", "jette", "jettes"],
    "fill": ["remplir", "verser", "recharger"],
    "help": ["aide", "indice"],
    "other": [],
}

# What the engine cannot know is left to the narrator (a language model): it understands the child.
WARNING = "Attention, ça risque de très mal finir pour ta mission… Tu es sûr de vouloir le faire ?"
TRUTH_NOTE = (
    "« à_savoir » est vrai dans l'aventure : glisse-le dans ton récit, avec tes mots (« On dirait bien qu'elle "
    "est fermée à clé… il faudrait trouver un moyen de l'ouvrir ! »), sans donner la solution."
)
FREE_NOTE = (
    "Pas prévu dans l'aventure, et ça ne change rien au jeu. Si c'est possible dans ce monde, joue le jeu : "
    "raconte-le en une ou deux phrases amusantes, puis reviens à la situation (« Maintenant que c'est fini, que "
    "fais-tu ? »). Si c'est impossible ici, dis-le avec une raison du monde. Si tu ne comprends pas ce qu'il "
    "veut faire, demande-lui de préciser."
)
ABSENT_NOTE = (
    "Pas dans l'aventure. Si c'est plausible ici (des cailloux dans des rochers, un bâton dans un bois, son "
    "doudou), joue le jeu : il peut en trouver et s'amuser avec, mais ça ne servira pas à résoudre l'aventure "
    "(rien n'est gagné ni ouvert). Si ce n'est pas plausible ici, dis-le gentiment ; si c'est bizarre, demande-lui "
    "ce qu'il veut dire. Le « décor » du lieu donne des idées, tu peux en imaginer d'autres."
)
DESTROY_NOTE = (
    "C'est fait, pour de bon : raconte-le. Si ça empêche de finir la mission, l'enfant s'en rendra compte plus tard "
    "(ne le dis pas maintenant)."
)
DARK_NOTE = (
    "Il fait noir ici : le héros ne voit rien, mais il sent les passages sous ses mains. Ne raconte que ce qu'il "
    "entend, sent ou touche (« sens »), jamais un objet ni un personnage (aucun n'est à portée) : il lui faut de "
    "la lumière pour voir."
)

BAG = {"sac", "sac a dos", "poche", "poches", "cartable", "besace", "mon sac", "ma poche"}  # the hero's bag

EXIT_STOPWORDS = {
    "vers",
    "chez",
    "voir",
    "dans",
    "cote",
    "aller",
    "allez",
    "par",
    "pour",
    "sur",
    "jusqu",
    "endroit",
    "ici",
}

# (« touch » is its own verb: a cuddle is not a push, and must never trigger « pousser le bonhomme ».)
# Spoken verbs for the strongest hint (« Essaie : donner l'algue à Pinces »).
VERB_FR = {"give": "donner", "use": "utiliser", "combine": "assembler", "open": "ouvrir", "close": "fermer",
           "talk": "parler à", "ask": "demander à", "eat": "manger", "drink": "boire", "climb": "grimper sur",
           "swim": "nager dans", "jump": "sauter", "push": "pousser", "pull": "tirer", "light": "allumer",
           "wear": "mettre", "read": "lire", "hit": "taper", "smell": "sentir", "listen": "écouter", "touch": "caresser",
           "wait": "attendre", "sit": "s'asseoir sur", "sing": "chanter pour", "dance": "danser avec",
           "throw": "lancer", "fill": "remplir", "search": "fouiller", "examine": "regarder"}  # fmt: skip


def canonical_verb(verb: str) -> str:
    """The canonical verb: as given (the narrator picks from the list), or one of its French words
    exactly; "other" otherwise. No guessing from the start of a word."""
    v = normalize_text(verb or "").strip()
    if v in VERBS:
        return v
    return next((canon for canon, words in VERBS.items() if v in words), "other")


def _norm(s: str) -> str:
    s = normalize_text(s or "")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = re.sub(r"\b(le|la|les|l'|un|une|des|du|de|d'|au|aux|a|mon|ma|mes|ton|ta|tes|son|sa|ses|ce|cette)\b", " ", s)
    s = s.replace("'", " ")
    return " ".join(s.split())


def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if len(w) > 2}


def _way(s: str) -> set[str]:
    """The words that name a way, without « vers », « chez », « aller »… (singular)."""
    return {w[:-1] if len(w) > 3 and w[-1] in "sx" else w for w in _words(s) - EXIT_STOPWORDS}


def _stems(s: str) -> set[str]:
    """Words without their plural mark: « des poissons » shows « un poisson »."""
    return {w[:-1] if len(w) > 3 and w[-1] in "sx" else w for w in _words(s)}


def of(name: str) -> str:
    """« de » + the name, contracted: du jardin, des rochers, de la cabane, de l'épave."""
    for article, contracted in (("le ", "du "), ("les ", "des ")):
        if name.lower().startswith(article):
            return contracted + name[len(article) :]
    return "de " + name


_cap = cap  # (kept for the modules that import it from here)


def bare(name: str) -> str:
    """The name without its article: « des cailloux » → « cailloux »."""
    return re.sub(r"^(des|les|du|de la|de l'|le|la|l')\s*", "", name)


def plural(name: str) -> bool:
    """« des lucioles », « les algues »: several things (« les attraper », not « l'attraper »)."""
    return _norm_keep(name).startswith(("des ", "les "))


def _norm_keep(s: str) -> str:
    """Lower case, no accents, articles kept (for « mon », « ma »…)."""
    return normalize_text(s or "").strip()


LOCKED_WORDS = re.compile(r"\b(ferm[ée]+s? (a|à) cl[ée]|verrouill)", re.I)


# The narrator reads outcomes with French keys (`storyworld.locale.localize`).
KEYS = {
    "place": "lieu",
    "here": "ici",
    "people": "personnages",
    "held": "tenu",
    "exits": "sorties",
    "to_label": "vers",
    "closed": "fermée",
    "decor": "décor",
    "memories": "souvenirs",
    "in_reach": "à_portée",
    "worth_exploring": "à_explorer",
    "you_are": "tu_es",
    "arrival": "arrivée",
    "inventory": "inventaire",
    "says": "dit",
    "where": "où",
    "to_know": "à_savoir",
    "not_here": "pas_ici",
    "choices": "choix",
    "tries_before_hint": "essais_avant_indice",
    "inside": "dedans",
    "reminder": "rappel",
    "hint_given": "indice",
    "ideas": "idees",
    "bag": "sac",
    "in_the_dark": "dans_le_noir",
    "senses": "sens",
    "uses_left": "utilisations_restantes",
}


# The engine's messages: its English source text (the message id) → the French said to the child or
# the narrator. Same named placeholders; `{of_place}` is already « du phare », `{Name}` capitalized.
MESSAGES: dict[str, str] = {
    # what changed (effects)
    "gained: {thing}": "gagné : {thing}",
    "lost: {thing}": "perdu : {thing}",
    "{who} lets go: {thing}": "{who} lâche : {thing}",
    "appears here: {thing}": "apparaît ici : {thing}",
    "follows you: {who}": "te suit : {who}",
    "inside: {thing}": "dedans : {thing}",
    "gone: {thing}": "disparu : {thing}",
    "{thing}: {n} left": "{thing} : encore {n} fois",
    "empty: {thing}": "vide : {thing}",
    "full again: {thing}": "de nouveau plein : {thing}",
    "{Name} is empty.": "{Name} est vide.",
    "{thing} ({n} uses left)": "{thing} (encore {n} fois)",
    "It's too dark to see that: you'd need some light.": "Il fait trop noir pour voir ça : il te faudrait de la lumière.",
    "It's dark here.": "Il fait tout noir ici.",
    # what is around
    "{thing} (in your bag)": "{thing} (dans ton sac)",
    "{who} holds {thing}": "{who} tient {thing}",
    "{Name} is locked.": "{Name} est fermée à clé.",
    "{Name} is closed.": "{Name} est fermée.",
    "blocked": "bloquée",
    "{about}: {note}": "{about} : {note}",
    "nothing": "rien",
    "closed": "fermé",
    # questions back to the child
    "with what / on what?": "avec quoi / sur quoi ?",
    "which one?": "lequel ?",
    "on what / to whom?": "sur quoi / à qui ?",
    "which way?": "par où ?",
    "who are you answering?": "tu réponds à qui ?",
    # the turn
    "It's already done.": "C'est déjà fait.",
    "Not yet… something is missing.": "Pas encore… il manque quelque chose.",
    "The adventure is already over.": "L'aventure est déjà finie.",
    "Without {thing}, there's no going further: the mission ends here.": (
        "Sans {thing}, impossible d'aller plus loin : la mission s'arrête là."
    ),
    "Take a good look around you.": "Regarde bien autour de toi.",
    "You'd rather keep your bag on your back: it might still come in handy!": (
        "Tu préfères garder ton sac sur ton dos : il pourrait encore servir !"
    ),
    "There are {decor} here, it's scenery: he can take some or play with them, but it doesn't help solve the "
    "adventure (nothing is gained).": (
        "Il y a des {decor} ici, c'est du décor : il peut en prendre ou jouer avec, "
        "mais ça ne sert pas à résoudre l'aventure (rien n'est gagné)."
    ),
    "You see {name}, but you can't catch them like that. You need to find a way.": (
        "Tu vois {name}, mais tu ne peux pas les attraper comme ça. Il faut trouver un moyen."
    ),
    "You see {name}, but you can't catch it like that. You need to find a way.": (
        "Tu vois {name}, mais tu ne peux pas l'attraper comme ça. Il faut trouver un moyen."
    ),
    " (He saw it elsewhere in the adventure.)": " (Il l'a vu ailleurs dans l'aventure.)",
    "It's locked.": "C'est fermé à clé.",
    "It's in {box}, and it's closed.": "C'est dans {box}, et c'est fermé.",
    # where things are
    "Say only this: never guess where things are.": "Dis seulement ça : ne devine jamais où sont les choses.",
    "You know of nothing like that in this adventure.": "Tu ne connais rien de tel dans cette aventure.",
    "You are there: {name}.": "Tu y es : {name}.",
    "You haven't been there yet: explore to find {name}!": "Tu n'y es pas encore allé : explore pour trouver {name} !",
    "To go to {name}: {way}.": "Pour aller vers {name} : {way}.",
    "{Name}, you've already been there.": "{Name}, tu y es déjà allé.",
    "{Name} is in your bag.": "{Name} est dans ton sac.",
    "{Name} is gone for good.": "{Name} a disparu pour de bon.",
    "{Name} is right here.": "{Name} est juste ici.",
    "You saw {name} near {of_place}.": "Tu as vu {name} du côté {of_place}.",
    "You haven't seen {name} yet. Explore, or ask someone!": (
        "Tu n'as pas encore vu {name}. Explore, ou demande à quelqu'un !"
    ),
    # going
    "There's no way out over there.": "Il n'y a pas de sortie par là.",
    "You're right next to {thing}.": "Tu es juste à côté de {thing}.",
    "It's not a way: it's here. To get on it or in it, the verb is climb.": (
        "Ce n'est pas un chemin : c'est ici. Pour monter dessus ou dedans, c'est le verbe climb."
    ),
    "You can't go that way.": "On ne peut pas aller par là.",
    "The way is blocked.": "Le passage est bloqué.",
    # opening, riddles, codes
    "It doesn't open.": "Ça ne s'ouvre pas.",
    "It's already open.": "C'est déjà ouvert.",
    "You need to find the right code.": "Il faut trouver le bon code.",
    "Well done, that's it!": "Bravo, c'est ça !",
    "Hmm… no, that's not it. Think again!": "Hmm… non, ce n'est pas ça. Réfléchis encore !",
    "{Name} is already open.": "{Name} est déjà ouvert.",
    "Click!": "Clic !",
    "Clack! It opens.": "Clac ! Ça s'ouvre.",
    "Bzzz! That's not the right order: it all starts again.": "Bzzz ! Ce n'est pas le bon ordre : tout recommence.",
    "Wrong order: the code starts again. The order is somewhere in the adventure.": (
        "Mauvais ordre : le code recommence. L'ordre se trouve quelque part dans l'aventure."
    ),
    # destroying, taking, leaving
    "You can't make {who} vanish like that!": "On ne fait pas disparaître {who} comme ça !",
    "{who} has it.": "C'est {who} qui l'a.",
    "It's far too sturdy for that.": "C'est bien trop solide pour ça.",
    "You already have {thing}.": "Tu as déjà {thing}.",
    "{who} has it. You need to find a way to get it.": "C'est {who} qui l'a. Il faut trouver un moyen de l'avoir.",
    "You can't take that with you.": "On ne peut pas emporter ça.",
    "Better leave it open: it might still come in handy!": "Mieux vaut laisser ouvert : ça pourrait encore servir !",
    "You'd rather keep it in your bag: it might come in handy!": (
        "Tu préfères le garder dans ton sac : il pourrait servir !"
    ),
    "A riddle: ask the question, and pass the answer with the verb answer.": (
        "Une devinette : pose la question, et passe sa réponse avec le verbe answer."
    ),
    # what is true (for the narrator)
    "{Name} has a code: you need to find the right one.": "{Name} a un code : il faut trouver le bon.",
    "{Name} only opens with the answer to its riddle: {question}": (
        "{Name} ne s'ouvre qu'avec la réponse à sa devinette : {question}"
    ),
    "It's locked, and {key} is in your bag.": "C'est fermé à clé, et {key} est dans ton sac.",
    "It's locked: you'd need a way to open it.": "C'est fermé à clé : il faudrait trouver un moyen de l'ouvrir.",
    "{Name} is waiting for the answer to their riddle.": "{Name} attend la réponse à sa devinette.",
    # reminders and hints
    "open {thing}": "ouvrir {thing}",
    "{verb} {thing}": "{verb} {thing}",
    "What if you went to look somewhere else?": "Et si tu allais voir ailleurs ?",
    "Maybe there's something near {of_place}…": "Il y a peut-être quelque chose du côté {of_place}…",
    "Go to {place}.": "Va vers {place}.",
    "Did you notice {thing}?": "Tu as remarqué {thing} ?",
    "Take {thing}.": "Prends {thing}.",
    "Listen carefully to the question of {of_who}… the answer may be hidden in the adventure.": (
        "Écoute bien la question {of_who}… la réponse est peut-être cachée dans l'aventure."
    ),
    "What if you talked to {who}?": "Et si tu parlais à {who} ?",
    "Answer: “{answer}”.": "Réponds : « {answer} ».",
    "{Name} has a code… Do you remember what you saw?": "{Name} a un code… Tu te souviens de ce que tu as vu ?",
    "Press {thing}.": "Appuie sur {thing}.",
    "And {thing}, what's inside?": "Et {thing}, qu'est-ce qu'il y a dedans ?",
    "Open {thing}.": "Ouvre {thing}.",
    "Think of what's in your bag, and who or what it could be useful for.": (
        "Pense à ce que tu as dans ton sac, et à qui ou à quoi ça pourrait servir."
    ),
    "And {thing}, what could it be useful for here?": "Et {thing}, à quoi ça pourrait servir ici ?",
    "Try something with what's here.": "Essaie quelque chose avec ce qui est là.",
    "Try: {verb} {thing}.": "Essaie : {verb} {thing}.",
    "Try: {verb} {thing}, with {target}.": "Essaie : {verb} {thing}, avec {target}.",
    "What if you went back near {of_place}? There's a clue there.": (
        "Et si tu retournais du côté {of_place} ? Il y a un indice là-bas."
    ),
    "What if you talked to {who} again? They may have given you a clue.": (
        "Et si tu reparlais à {who} ? Il t'a peut-être donné un indice."
    ),
    "What if you went back to see {thing}, near {of_place}? There's a clue.": (
        "Et si tu retournais voir {thing}, du côté {of_place} ? Il y a un indice."
    ),
    "What if you looked at {thing} again? There's a clue.": "Et si tu regardais encore {thing} ? Il y a un indice.",
}

FRENCH = Language(
    code="fr",
    messages=MESSAGES,
    verbs=VERBS,
    verb_words=VERB_FR,
    keys=KEYS,
    warning=WARNING,
    truth_note=TRUTH_NOTE,
    free_note=FREE_NOTE,
    absent_note=ABSENT_NOTE,
    destroy_note=DESTROY_NOTE,
    dark_note=DARK_NOTE,
    of=of,
    bare=bare,
    plural=plural,
)
