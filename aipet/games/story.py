"""Stories with a real plan, a framed length, and a memory of characters and universes.

- `story_start(idea)`: a plan is written by a model call (title, universe, characters, a few
  steps each with a key event and a choice for the child, a direction for the ending), adapted
  to the child's level and the time of day (evening: calmer and shorter). Recurring characters
  the child loves are brought back and grown. An unfinished story can be resumed.
- The plan stays in the system prompt while the story runs (consistency even when the history
  is trimmed); `story_next` moves to the next step; the message label shows « étape 3 sur 5 »
  and asks to wrap up after a time limit.
- `story_end(reaction, …)`: records how the child liked it, their favourite characters and what
  the characters became. Stored in the family memory as `histoires/univers.json` (data, French).
- Real people (the children, the pet) are never invented characters: they may be « guests » of a
  story, in a role for that story only (« Noam, en petit lapin »), when the child asks or at most
  once every GUEST_EVERY stories. The code keeps them out of the recurring characters, whatever
  the model writes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from loguru import logger

from aipet import character, engines
from aipet.games.common import (
    PREPARING_NOTE,
    STALE_PREPARATION,
    UNKNOWN_PLAYER,
    JsonStore,
    cancel_preparation,
    child_level,
    current_player,
    is_stale,
    new_preparation,
    pet_intro,
    theme_key,
)
from aipet.memory.store import now
from aipet.session_log import NO_LOG

STORY_TOOLS = {"story_start", "story_next", "story_end"}
STEPS_DAY, STEPS_SHORT = 5, 4  # short: evening or little ones
MAX_MINUTES = 15
RECURRING_SHOWN = 6
LITTLE_ONES = {"PS", "MS", "GS", "CP"}
GUEST_EVERY = 3  # children in a story: when asked, otherwise at most one story in three

# Model-facing (French).
PLAN_INSTRUCTIONS = """Tu écris le PLAN d'une histoire que {pet}, va raconter à voix haute à un enfant,
par petits morceaux, en lui laissant faire des choix. Pas la narration : seulement les éléments qui guident.
Règles :
- Histoire adaptée à l'âge, qui finit bien. Pas de vraie peur, pas de violence ; un petit problème qui se
  règle avec de l'entraide, de l'astuce ou de l'amitié.
- Personnages (« characters ») : 2 à 4 personnages INVENTÉS, chacun avec un nom facile à dire, une
  description courte et un trait qui le rend unique. Jamais un vrai enfant ni {pet_name} : eux sont des
  invités (« guests »), seulement si la demande dit qu'ils peuvent l'être, avec un rôle pour cette
  histoire (eux-mêmes ou un costume, ex. « un petit lapin »), toujours valorisant, jamais celui qui pose
  problème. Si des personnages récurrents sont proposés et qu'ils vont avec l'idée, reprends-en un ou deux
  (même nom) et fais-les grandir : un nouveau trait, une nouvelle facette.
- Étapes : exactement le nombre demandé. Chaque étape a un événement clé (une ou deux phrases) et un choix
  simple à proposer à l'enfant (deux possibilités), sauf la dernière.
- La fin : une direction (pas une fin figée, l'enfant aura influencé l'histoire).
- Le soir : ton doux et calme, fin apaisante (tout le monde s'endort, rentre au chaud…)."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "universe": {"type": "string", "description": "le monde de l'histoire, en une ou deux phrases"},
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "trait": {"type": "string"},
                    "role": {"type": "string", "description": "héros, ami, celui qui pose problème…"},
                },
                "required": ["name", "description", "trait", "role"],
            },
        },
        "guests": {
            "type": "array",
            "description": "les vrais enfants (ou le pet) dans l'histoire, seulement si c'est permis",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "son vrai prénom"},
                    "role": {"type": "string", "description": "son rôle pour cette histoire seulement"},
                },
                "required": ["name", "role"],
            },
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event": {"type": "string"},
                    "choice": {"type": "string", "description": "deux possibilités pour l'enfant, ou vide à la fin"},
                },
                "required": ["event", "choice"],
            },
        },
        "ending": {"type": "string"},
    },
    "required": ["title", "universe", "characters", "steps", "ending"],
}


def generate_plan(
    cfg: dict,
    idea: str,
    who: str,
    steps: int,
    evening: bool,
    recurring: list[str],
    past: list[str],
    guests: list[str] | None = None,
) -> dict:
    """`guests`: the real people who may appear in this story (none: nobody real)."""
    pet_name = character.of(cfg).name
    invited = (
        f"Invités possibles (de vrais enfants, et {pet_name}) : {', '.join(guests)}. Mets-en un ou plusieurs "
        "dans « guests » si ça va avec l'idée."
        if guests
        else f"Pas d'invités cette fois : ni les enfants ni {pet_name} ne sont dans l'histoire (« guests » vide)."
    )
    prompt = (
        f"Idée de l'enfant : {idea or 'surprends-moi'}\nPour : {who}\nNombre d'étapes : {steps}\n"
        f"Moment : {'le soir, avant de dormir' if evening else 'la journée'}\n{invited}\n\n"
        "Personnages récurrents que l'enfant connaît (reprends-en un ou deux s'ils vont avec l'idée) :\n"
        + ("\n".join(f"- {c}" for c in recurring) or "(aucun)")
        + "\n\nTitres des histoires déjà racontées (ne refais pas la même) :\n"
        + ("\n".join(f"- {t}" for t in past) or "(aucune)")
    )
    plan = engines.complete_json(
        cfg,
        PLAN_INSTRUCTIONS.format(pet=pet_intro(cfg), pet_name=pet_name),
        prompt,
        PLAN_SCHEMA,
        role="preparation",
        activity="story",
        timeout=40,
    )
    plan["steps"] = [s for s in plan.get("steps", []) if s.get("event")][:steps]
    return plan


class StoryGame:
    def __init__(self, cfg: dict, memory_root, keeper=None, slog=None):
        self.cfg, self.root, self.keeper, self.slog = cfg, memory_root, keeper, slog or NO_LOG
        self.store = JsonStore(memory_root / "histoires" / "univers.json", {"characters": {}, "stories": []})
        self.story: dict | None = None  # the running story (also saved, to resume later)
        self.started_at: datetime | None = None

    def _ev(self, type_, **kw):
        self.slog.event(type_, **kw)

    # ------------------------------------------------------------------ memory
    def recurring(self, player: str) -> list[str]:
        """Characters worth bringing back, most loved first (French lines for the planner)."""
        chars = self.store.load()["characters"].values()
        ranked = sorted(chars, key=lambda c: (-c.get("liked_by", {}).get(player, 0), -c.get("appearances", 0)))
        out = []
        for c in ranked[:RECURRING_SHOWN]:
            loved = c.get("liked_by", {}).get(player, 0)
            traits = ", ".join(c.get("traits", [])[-4:])
            out.append(f"{c['name']} : {c['description']} ({traits}) — {c.get('appearances', 0)} histoire(s)"
                       + (f", adoré par l'enfant ({loved} fois)" if loved else ""))  # fmt: skip
        return out

    def unfinished(self, player: str) -> dict | None:
        stories = [s for s in self.store.load()["stories"] if s["player"] == player and not s.get("finished")]
        return stories[-1] if stories else None

    def _save_story(self, story: dict):
        with self.store.lock:
            data = self.store.load()
            data["stories"] = [s for s in data["stories"] if s["id"] != story["id"]] + [story]
            self.store.save(data)

    # ------------------------------------------------------------------ actions
    # ------------------------------------------------------------------ real people
    def _real_names(self) -> set[str]:
        """Normalized names of the real people: family cards, children met, the pet."""
        names = {p.stem for p in (self.root / "famille").glob("*.md")}
        k = self.keeper
        if k:
            if hasattr(k, "confirmed_names"):
                names |= k.confirmed_names()
            who = getattr(k, "who", None)
            if who:
                names |= {n for n in [who.current, *getattr(who, "present", [])] if n}
        try:
            names.add(character.of(self.cfg).name)
        except Exception as e:  # no pet pack: the other names still protect the children
            logger.debug(f"Story: no pet name ({e})")
        return {theme_key(n) for n in names if n}

    @staticmethod
    def _is_real(name: str, real: set[str]) -> bool:
        """« Noam », « Noam le lapin », « Flocon » are real people; « Piko » is not."""
        key = theme_key(name or "")
        return key in real or bool(set(key.split()) & real)

    def _present(self, player: str) -> list[str]:
        who = getattr(self.keeper, "who", None)
        present = [n for n in [*getattr(who, "present", []), player] if n and n != UNKNOWN_PLAYER]
        return list(dict.fromkeys(present))

    def _guests_allowed(self, player: str, asked: bool) -> bool:
        if asked:
            return True
        mine = [s for s in self.store.load()["stories"] if s.get("player") == player]
        return not any(s.get("plan", {}).get("guests") for s in mine[-(GUEST_EVERY - 1) :])

    async def start(self, idea: str = "", resume: bool = False, with_children: bool = False) -> dict:
        player = current_player(self.keeper)
        if resume:
            story = self.unfinished(player)
            if not story:
                return {"error": "Pas d'histoire en cours à reprendre : propose d'en commencer une nouvelle."}
            self.story, self.started_at = story, now()
            self._refresh()
            step = story["step"]
            return {
                "resumed": story["title"],
                "summary_so_far": story.get("happened", []),
                "step": f"{step + 1} sur {len(story['plan']['steps'])}",
                "note": "Rappelle en deux phrases où on en était, puis reprends l'histoire à cette étape.",
            }
        if self.story:
            self.stop()
        today = now().date()
        level = child_level(self.root, player, today) if player != UNKNOWN_PLAYER else None
        rhythm = getattr(self.keeper, "rhythm", None)
        evening = bool(rhythm and (rhythm.is_night(now()) or rhythm.minutes_to_bedtime(now()) <= 90))
        steps = STEPS_SHORT if evening or level in LITTLE_ONES else STEPS_DAY
        who = f"{player}, en {level}" if level else (player if player != UNKNOWN_PLAYER else "un enfant de 5 à 10 ans")
        past = [s["title"] for s in self.store.load()["stories"]][-15:]
        prep = new_preparation(self)
        present = self._present(player)
        guests = None
        if present and self._guests_allowed(player, with_children):
            try:
                guests = [*present, character.of(self.cfg).name]
            except Exception:
                guests = present
        try:
            plan = await asyncio.to_thread(
                generate_plan, self.cfg, idea, who, steps, evening, self.recurring(player), past, guests
            )
        except Exception as e:
            logger.warning(f"Story plan failed: {e}")
            return {
                "error": "L'histoire n'a pas pu être préparée : raconte-en une simple toi-même, ou propose autre chose."
            }
        if is_stale(self, prep):
            return STALE_PREPARATION
        if len(plan.get("steps", [])) < 3:
            return {"error": "Plan incomplet : raconte une histoire simple toi-même."}
        real = self._real_names()
        plan["characters"] = [c for c in plan.get("characters", []) if not self._is_real(c.get("name", ""), real)]
        allowed = {theme_key(n) for n in guests or []}
        plan["guests"] = [g for g in plan.get("guests") or [] if g.get("name") and self._is_real(g["name"], allowed)]
        self.story = {
            "id": uuid.uuid4().hex[:8], "player": player, "idea": idea, "date": now().isoformat(timespec="minutes"),
            "evening": evening, "title": plan["title"], "plan": plan, "step": 0, "happened": [], "finished": False,
        }  # fmt: skip
        self.started_at = now()
        self._save_story(self.story)
        self._note_characters(plan["characters"], appeared=True)
        self._refresh()
        logger.info(f"📖 Story « {plan['title']} » for {player}: {len(plan['steps'])} steps, evening={evening}")
        self._ev("story_start", title=plan["title"], player=player, steps=len(plan["steps"]), evening=evening)
        return {
            "title": plan["title"],
            "steps": len(plan["steps"]),
            "note": "Le plan est dans tes consignes (« Histoire en cours »). Commence l'étape 1 : présente le monde "
            "et le héros en trois ou quatre phrases, puis propose le choix de l'étape.",
        }

    def next_step(self, what_happened: str = "", child_ideas: str = "", step_done: int | None = None) -> dict:
        s = self.story
        if not s:
            return {"error": "Aucune histoire en cours : appelle story_start."}
        if step_done is not None and int(step_done) != s["step"] + 1:
            return {"error": f"L'étape {step_done} est déjà passée : on en est à l'étape {s['step'] + 1}."}
        s["happened"].append(" ".join(x for x in (what_happened.strip(), child_ideas.strip()) if x) or "…")
        s["step"] += 1
        self._save_story(s)
        self._refresh()
        total = len(s["plan"]["steps"])
        if s["step"] >= total:
            return {"note": "C'était la dernière étape : raconte la fin (en tenant compte de ses choix), puis demande "
                    "si l'histoire lui a plu et quel personnage il a préféré, et appelle story_end."}  # fmt: skip
        step = s["plan"]["steps"][s["step"]]
        return {"step": f"{s['step'] + 1} sur {total}", "event": step["event"], "choice": step.get("choice", "")}

    def end(
        self, reaction: str = "", favourite_characters: list | None = None, character_updates: list | None = None
    ) -> dict:
        s = self.story
        if not s:
            return {"error": "Aucune histoire en cours."}
        s["finished"], s["reaction"] = True, reaction.strip()
        s["favourites"] = [f for f in (favourite_characters or []) if f]
        self._save_story(s)
        self._note_characters(
            [{"name": u.get("name", ""), "trait": u.get("new_trait", "")} for u in (character_updates or [])],
            appeared=False,
        )
        self._note_liked(s["player"], s["favourites"])
        self._ev("story_end", title=s["title"], player=s["player"], reaction=s["reaction"], favourites=s["favourites"])
        self.story = None
        self._refresh()
        return {"saved": s["title"], "note": "Remercie l'enfant et propose autre chose (ou bonne nuit le soir)."}

    def stop(self):
        """The activity ends mid-story: it stays unfinished, to be resumed another time."""
        cancel_preparation(self)
        if self.story:
            self._save_story(self.story)
            self._ev("story_paused", title=self.story["title"], step=self.story["step"])
        self.story = None

    # ------------------------------------------------------------------ characters
    def _note_characters(self, characters: list[dict], appeared: bool):
        with self.store.lock:
            data = self.store.load()
            real = self._real_names()
            for c in characters:
                name = (c.get("name") or "").strip()
                if not name or self._is_real(name, real):
                    continue  # real people are never recurring characters
                key = theme_key(name)
                entry = data["characters"].setdefault(
                    key, {"name": name, "description": c.get("description", ""), "traits": [], "appearances": 0,
                          "liked_by": {}, "first_seen": now().date().isoformat()},
                )  # fmt: skip
                if c.get("description") and not entry.get("description"):
                    entry["description"] = c["description"]
                trait = (c.get("trait") or "").strip()
                if trait and trait not in entry["traits"]:
                    entry["traits"] = (entry["traits"] + [trait])[-8:]
                if appeared:
                    entry["appearances"] += 1
                    entry["last_seen"] = now().date().isoformat()
            self.store.save(data)

    def _note_liked(self, player: str, names: list[str]):
        with self.store.lock:
            data = self.store.load()
            real = self._real_names()
            for n in names:
                if self._is_real(n, real):
                    continue
                c = data["characters"].get(theme_key(n))
                if c:
                    c["liked_by"][player] = c["liked_by"].get(player, 0) + 1
            self.store.save(data)

    # ------------------------------------------------------------------ prompt and labels
    def _refresh(self):
        if self.keeper and getattr(self.keeper, "worker", None):
            asyncio.get_running_loop().create_task(self.keeper.refresh_prompt())

    def prompt_section(self) -> str:
        """The running story's plan, kept in the system prompt (French)."""
        s = self.story
        if not s:
            return ""
        p = s["plan"]
        chars = "\n".join(f"- {c['name']} ({c['role']}) : {c['description']} ; {c['trait']}" for c in p["characters"])
        steps = "\n".join(
            f"{'→ ' if i == s['step'] else '  '}{i + 1}. {st['event']}" + (f" (choix : {st['choice']})" if st.get("choice") else "")
            + (" [fait]" if i < s["step"] else "")
            for i, st in enumerate(p["steps"])
        )  # fmt: skip
        happened = "\n".join(f"- {h}" for h in s["happened"]) or "(on commence)"
        guests = p.get("guests") or []
        guests_txt = (
            "Les invités (de vrais enfants, ou toi), dans un rôle pour cette histoire seulement :\n"
            + "\n".join(f"- {g['name']} : {g['role']}" for g in guests)
            if guests
            else "Les enfants et toi n'êtes pas des personnages de cette histoire (sauf si l'enfant le demande)."
        )
        return (
            f"## Histoire en cours : « {s['title']} »\n"
            "Raconte-la par petits morceaux de trois ou quatre phrases, avec des bruits et du suspense, en suivant "
            "ce plan (la narration est libre, mais garde les noms, les personnages et le monde). Intègre les idées "
            "de l'enfant. Quand l'étape en cours est finie (son événement est raconté et l'enfant a fait son "
            "choix), appelle story_next.\n"
            f"Le monde : {p['universe']}\nLes personnages :\n{chars}\n{guests_txt}\nLes étapes :\n{steps}\n"
            f"La fin, vers : {p['ending']}\nCe qui s'est passé jusqu'ici :\n{happened}"
        )

    def on_user_message(self, text: str) -> str | None:
        s = self.story
        if not s or not self.started_at:
            return None
        minutes = (now() - self.started_at).total_seconds() / 60
        hint = f"histoire : étape {min(s['step'] + 1, len(s['plan']['steps']))} sur {len(s['plan']['steps'])}"
        if minutes >= MAX_MINUTES:
            hint += " ; il est temps de conclure : va vers la fin en une ou deux étapes"
        return hint


def register_tools(llm, game: StoryGame) -> list:
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    keeper = game.keeper

    async def start(params):
        if keeper and keeper.tool_budget_exhausted():
            await params.result_callback(keeper.TOOLS_EXHAUSTED)
            return
        a = params.arguments
        await params.result_callback(
            await game.start(a.get("idea", ""), bool(a.get("resume")), bool(a.get("with_children")))
        )

    async def nxt(params):
        a = params.arguments
        await params.result_callback(
            game.next_step(a.get("what_happened", ""), a.get("child_ideas", ""), a.get("step_done"))
        )

    async def end(params):
        a = params.arguments
        await params.result_callback(
            game.end(a.get("reaction", ""), a.get("favourite_characters"), a.get("character_updates"))
        )

    llm.register_function("story_start", start, cancel_on_interruption=False)  # see PREPARING_NOTE
    llm.register_function("story_next", nxt)
    llm.register_function("story_end", end)
    return [
        FunctionSchema(
            name="story_start",
            description="Prépare une histoire : écrit son plan (monde, personnages, étapes, fin) d'après l'idée de "
            "l'enfant, avec des personnages récurrents qu'il aime. resume=true pour reprendre l'histoire pas finie."
            + PREPARING_NOTE,
            properties={
                "idea": {
                    "type": "string",
                    "description": "l'idée de l'enfant (héros, lieu…), ou vide pour une surprise",
                },
                "resume": {"type": "boolean", "description": "true pour reprendre l'histoire interrompue"},
                "with_children": {
                    "type": "boolean",
                    "description": "true seulement si l'enfant demande à être dans l'histoire (lui, son frère, "
                    "sa sœur, toi…)",
                },
            },
            required=[],
        ),
        FunctionSchema(
            name="story_next",
            description="L'étape en cours de l'histoire est finie : passe à la suivante.",
            properties={
                "what_happened": {"type": "string", "description": "ce qui s'est passé, en une phrase"},
                "child_ideas": {"type": "string", "description": "les choix ou idées de l'enfant"},
                "step_done": {"type": "integer", "description": "le numéro de l'étape qui vient de se terminer"},
            },
            required=["what_happened", "step_done"],
        ),
        FunctionSchema(
            name="story_end",
            description="Fin de l'histoire, après avoir demandé à l'enfant si elle lui a plu.",
            properties={
                "reaction": {"type": "string", "description": "ce que l'enfant en a dit, ex. « il a adoré »"},
                "favourite_characters": {"type": "array", "items": {"type": "string"}},
                "character_updates": {
                    "type": "array",
                    "description": "ce que les personnages sont devenus (nouveau trait, nouvel ami…)",
                    "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "new_trait": {"type": "string"}},
                        "required": ["name", "new_trait"],
                    },
                },
            },
            required=["reaction"],
        ),
    ]
