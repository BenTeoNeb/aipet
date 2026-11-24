"""How the turn treats verbs: language-independent classes of the canonical verbs."""

from __future__ import annotations

# Verbs that mean « put this thing to work on that one »: a scripted « give fish to troll » also
# answers « lancer le poisson au troll » or « utiliser le poisson sur le troll ».
APPLY = {"use", "give", "combine", "open", "light", "throw", "fill", "wear", "push", "pull", "hit", "read", "touch"}
SYMMETRIC = {"combine"}
IDEMPOTENT = {
    "flag",
    "reveal",
    "move",
    "follow",
    "goto",
    "end",
    "open",
    "unlock",
    "set",
    "refill",
}  # doing it again changes nothing
HELD_VERBS = {
    "give",
    "use",
    "combine",
    "throw",
    "fill",
    "light",
    "wear",
    "eat",
    "drink",
}  # the thing must be in hand  # combine A with B = combine B with A
TALK = {"talk", "ask"}
LOOK_AT = {"examine", "look", "search", "smell", "listen", "read"}
# Verbs that need a real thing: « prendre une voiture » there is none; « chanter une chanson » is imagination.
NEEDS_A_THING = {"take", "drop", "give", "destroy", "use", "combine", "open", "close", "eat", "drink", "wear", "light",
                 "throw", "fill", "push", "pull", "hit"}  # fmt: skip
NOTES_KEPT = 6  # memories per place, character or thing
STUCK = "_coince"  # the built-in end when the adventure can't be won any more (after « destroy »)
FAILED = {"not_yet", "impossible", "no_exit", "ask", "clue"}  # not « absent »: often imagination
NEVER_SCRIPTED = {
    "go",
    "take",
    "drop",
    "look",
    "inventory",
    "help",
    "other",
    "where",
    "improvise",
}  # built in: scripts can't use them
# The verb carries little once two things are named (a player over speech recognition: « j'attrape les
# lucioles avec le bocal » is « remplir le bocal de lucioles »): such an action is found by its things,
# whatever the verb, except for these, which mean something else (looking, talking, a cuddle, a blow,
# leaving, playing).
NO_REMAP = {"hit", "touch", "talk", "ask", "go", "drop", "close", "inventory", "help", "look", "examine", "search",
            "smell", "listen", "read", "other", "dance", "sing", "sit", "wait", "jump", "destroy", "where"}  # fmt: skip
OPENERS = {"open", "pull", "push", "use"}  # « tirer le tiroir » opens it
WAYS = {"climb", "swim", "jump"}  # with a `to` and no thing: going somewhere
SENSES = {"listen", "smell", "touch"}  # a place's or a thing's `senses`: what the hero hears, smells, feels
PRESSERS = {"push", "use", "touch", "hit", "pull", "open"}  # « appuyer / toucher / taper sur le bouton rouge »
