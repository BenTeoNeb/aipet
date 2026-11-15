"""Learning something new: short discovery lessons, adapted to the class, that go a bit further over time.

- `learn_suggest`: what the pet knows about this child's learning (domains and level per domain,
  recent topics, interests, what they do at school now, their open questions), to propose a topic.
- `learn_start(topic, domain)`: a mini-lesson plan is written by a model call (3-4 short
  discoveries with an everyday image and an « à ton avis ? » question, a link to a topic already
  seen, and a bonus challenge one step above the child's level). The plan stays in the system
  prompt during the lesson; `learn_next` moves on; the label shows « découverte 2 sur 4 ».
- `learn_end(...)`: records what the child retained, whether they liked it and their new
  questions; the domain level goes up after a well-understood lesson with the bonus challenge,
  down after a hard one. Stored in the family memory as `jeux/apprendre.json` (data, French).
"""

from __future__ import annotations

from loguru import logger

from aipet import engines
from aipet.games.common import (
    GAMES_DIR,
    PREPARING_NOTE,
    UNKNOWN_PLAYER,
    Activity,
    JsonStore,
    pet_intro,
    theme_key,
)
from aipet.memory.cards import LIKES, SCHOOL, card_path, child_level, parse_card
from aipet.timefmt import now

LEARN_TOOLS = {"learn_suggest", "learn_start", "learn_next", "learn_end"}
START_LEVEL = 3  # relative to the class: 3 = « de son niveau »
RECENT_TOPICS = 12
OPEN_QUESTIONS_KEPT = 15

# Model-facing (French).
LEVELS = {
    1: "très simple pour sa classe",
    2: "facile pour sa classe",
    3: "de son niveau de classe",
    4: "un peu au-delà de sa classe",
    5: "bien au-delà de sa classe, pour un passionné",
}
LESSON_INSTRUCTIONS = """Tu écris le PLAN d'une mini-leçon que {pet} qui sait beaucoup de choses, va
faire découvrir à voix haute à un enfant, comme une conversation (pas un cours). Pas la narration : les éléments
qui guident.
Règles :
- Uniquement des faits sûrs, vérifiés, adaptés au niveau demandé. Pas de date ou de chiffre douteux.
- 3 ou 4 découvertes, de la plus simple à la plus étonnante. Chacune : le fait clé en une ou deux phrases
  simples, une image ou comparaison du quotidien de l'enfant, et une question « à ton avis ? » à poser AVANT
  d'expliquer, pour le faire réfléchir.
- Un lien avec un sujet déjà vu, s'il y en a un qui s'y prête (sinon vide).
- Un défi bonus un cran au-dessus de son niveau : une question ou une idée qui l'emmène plus loin.
- Une petite question de fin pour vérifier qu'il a compris, sans en faire un contrôle."""

LESSON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "discoveries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "image": {"type": "string", "description": "comparaison avec le quotidien"},
                    "guess_question": {"type": "string", "description": "« à ton avis ? »"},
                },
                "required": ["fact", "image", "guess_question"],
            },
        },
        "link": {"type": "string", "description": "lien avec un sujet déjà vu, ou vide"},
        "bonus": {"type": "string"},
        "check_question": {"type": "string"},
    },
    "required": ["title", "discoveries", "link", "bonus", "check_question"],
}


def generate_lesson(cfg: dict, topic: str, domain: str, who: str, level: int, seen: list[str]) -> dict:
    prompt = (
        f"Sujet : {topic}\nDomaine : {domain or 'à toi de voir'}\nPour : {who}\n"
        f"Niveau : {level} sur 5, {LEVELS[level]}\n\nSujets déjà vus avec cet enfant :\n"
        + ("\n".join(f"- {t}" for t in seen) or "(aucun)")
    )
    plan = engines.complete_json(
        cfg,
        LESSON_INSTRUCTIONS.format(pet=pet_intro(cfg)),
        prompt,
        LESSON_SCHEMA,
        role="preparation",
        activity="learn",
        timeout=40,
    )
    plan["discoveries"] = [d for d in plan.get("discoveries", []) if d.get("fact")][:4]
    return plan


class LearnGame(Activity):
    tools = frozenset(LEARN_TOOLS)

    def register_tools(self, llm) -> list:
        return register_tools(llm, self)

    def __init__(self, cfg: dict, memory_root, host=None, slog=None):
        super().__init__(host, slog)
        self.cfg, self.root = cfg, memory_root
        self.store = JsonStore(memory_root / GAMES_DIR / "apprendre.json", {"players": {}})
        self.lesson: dict | None = None

    def _ev(self, type_, **kw):
        self.slog.event(type_, **kw)

    def _player(self, data: dict, player: str) -> dict:
        return data["players"].setdefault(player, {"domains": {}, "topics": [], "interests": {}, "open_questions": []})

    # ------------------------------------------------------------------ suggest
    def suggest(self) -> dict:
        player = self.host.player()
        p = self.store.load()["players"].get(player, {})
        card_likes, school_now = [], []
        card = card_path(self.root, player)
        if player != UNKNOWN_PLAYER and card.exists():
            _, sections = parse_card(card.read_text(encoding="utf-8"))
            card_likes = sections.get(LIKES, [])[-8:]
            school_now = [t.split(" : ", 1)[-1] for t in sections.get(SCHOOL, [])[-4:]]
        level = child_level(self.root, player, now().date()) if player != UNKNOWN_PLAYER else None
        interests = sorted(p.get("interests", {}).items(), key=lambda kv: -kv[1])
        return {
            "player": player,
            "class": level,
            "domains": [f"{d['name']} : niveau {d['level']} sur 5, {d['lessons']} leçon(s)" for d in p.get("domains", {}).values()],
            "recent_topics": [f"{t['topic']} ({t['domain']}, {'a aimé' if t.get('liked') else 'bof'})" for t in p.get("topics", [])[-RECENT_TOPICS:]],
            "loves_learning_about": [k for k, _ in interests[:6]],
            "likes": card_likes,
            "at_school_now": school_now,
            "open_questions": p.get("open_questions", [])[-6:],
            "note": "Propose deux idées au choix : une liée à ce qu'il aime, à ses questions ou à ce qu'il fait en "
            "classe, et une toute nouvelle pour élargir. Ne propose pas un sujet déjà vu, sauf pour aller plus loin.",
        }  # fmt: skip

    # ------------------------------------------------------------------ lesson
    async def start(self, topic: str, domain: str = "") -> dict:
        topic, domain = (topic or "").strip(), (domain or "").strip()
        if not topic:
            return {"error": "Demande d'abord ce qu'il veut découvrir."}
        player = self.host.player()
        data = self.store.load()
        p = data["players"].get(player, {})
        level = p.get("domains", {}).get(theme_key(domain), {}).get("level", START_LEVEL) if domain else START_LEVEL
        school = child_level(self.root, player, now().date()) if player != UNKNOWN_PLAYER else None
        who = (
            f"{player}, en {school}" if school else (player if player != UNKNOWN_PLAYER else "un enfant de 5 à 10 ans")
        )
        seen = [t["topic"] for t in p.get("topics", [])][-RECENT_TOPICS:]
        plan, refused = await self.prepare(
            generate_lesson, self.cfg, topic, domain, who, level, seen,
            failed="La leçon n'a pas pu être préparée : explique simplement toi-même, ou propose autre chose.",
        )  # fmt: skip
        if refused:
            return refused
        if len(plan.get("discoveries", [])) < 2:
            return {"error": "Plan incomplet : explique simplement toi-même."}
        self.lesson = {"player": player, "topic": topic, "domain": domain or "découverte", "level": level,
                       "plan": plan, "step": 0, "answers": []}  # fmt: skip
        self._refresh()
        logger.info(f"🎓 Lesson « {plan['title']} » for {player} ({domain}, level {level})")
        self._ev("learn_start", topic=topic, domain=domain, player=player, level=level)
        return {
            "title": plan["title"],
            "discoveries": len(plan["discoveries"]),
            "level": f"niveau {level} sur 5 ({LEVELS[level]})",
            "note": "Le plan est dans tes consignes (« Leçon en cours »). Commence par la question « à ton avis ? » "
            "de la première découverte.",
        }

    def next(self, understood: str = "", discovery_done: int | None = None) -> dict:
        les = self.lesson
        if not les:
            return {"error": "Aucune leçon en cours : appelle learn_start."}
        if discovery_done is not None and int(discovery_done) != les["step"] + 1:
            return {"error": f"La découverte {discovery_done} est déjà faite : on en est à la {les['step'] + 1}."}
        les["answers"].append(understood or "?")
        les["step"] += 1
        self._refresh()
        total = len(les["plan"]["discoveries"])
        if les["step"] >= total:
            return {"note": "Toutes les découvertes sont faites : propose le défi bonus, puis la petite question de fin. "
                    "Ensuite demande ce qu'il a retenu, si ça lui a plu et s'il a d'autres questions, et appelle learn_end."}  # fmt: skip
        d = les["plan"]["discoveries"][les["step"]]
        return {"discovery": f"{les['step'] + 1} sur {total}", "guess_question": d["guess_question"]}

    def end(
        self, retained: str = "", liked: bool = True, bonus_done: bool = False, new_questions: list | None = None
    ) -> dict:
        les = self.lesson
        if not les:
            return {"error": "Aucune leçon en cours."}
        understood_well = sum(1 for a in les["answers"] if a == "oui") >= max(1, len(les["answers"]) - 1)
        struggled = sum(1 for a in les["answers"] if a == "non") >= 2
        with self.store.lock:
            data = self.store.load()
            p = self._player(data, les["player"])
            key = theme_key(les["domain"])
            dom = p["domains"].setdefault(key, {"name": les["domain"], "level": les["level"], "lessons": 0})
            before = dom["level"]
            if understood_well and bonus_done:
                dom["level"] = min(5, before + 1)
            elif struggled:
                dom["level"] = max(1, before - 1)
            dom["lessons"] += 1
            p["topics"].append({"date": now().isoformat(timespec="minutes"), "topic": les["topic"], "domain": les["domain"],
                                "title": les["plan"]["title"], "retained": retained.strip(), "liked": bool(liked),
                                "answers": les["answers"]})  # fmt: skip
            if liked:
                p["interests"][les["domain"]] = p["interests"].get(les["domain"], 0) + 1
            p["open_questions"] = (p["open_questions"] + [q for q in (new_questions or []) if q])[-OPEN_QUESTIONS_KEPT:]
            self.store.save(data)
        self._ev("learn_end", topic=les["topic"], player=les["player"], liked=bool(liked), level=dom["level"])
        self.lesson = None
        self._refresh()
        return {
            "domain_level": dom["level"],
            "level_change": "up" if dom["level"] > before else "down" if dom["level"] < before else None,
            "note": 'Félicite-le pour ce qu\'il a retenu (avec ses mots). Si level_change vaut "up", dis-lui fièrement '
            "que la prochaine fois tu lui feras découvrir des choses de grand dans ce domaine. Propose autre chose.",
        }

    def stop(self):
        """The activity ends mid-lesson: nothing is recorded (the child did not finish)."""
        self.cancel_preparation()
        self.lesson = None

    # ------------------------------------------------------------------ prompt and labels
    def _refresh(self):
        self.host.refresh_prompt_soon()

    def prompt_section(self) -> str:
        les = self.lesson
        if not les:
            return ""
        p = les["plan"]
        steps = "\n".join(
            f"{'→ ' if i == les['step'] else '  '}{i + 1}. {d['fact']} (image : {d['image']} ; à ton avis : "
            f"{d['guess_question']})" + (" [fait]" if i < les["step"] else "")
            for i, d in enumerate(p["discoveries"])
        )
        return (
            f"## Leçon en cours : « {p['title']} »\n"
            "Fais découvrir, ne fais pas cours : pour chaque découverte, pose d'abord la question « à ton avis ? », "
            "écoute sa réponse, puis explique en deux ou trois phrases avec l'image du quotidien. Félicite les bonnes "
            "idées, même incomplètes. Quand une découverte est faite, appelle learn_next (understood : oui, un peu "
            "ou non).\n"
            f"Les découvertes :\n{steps}\n"
            + (f"Lien avec ce qu'il a déjà vu : {p['link']}\n" if p.get("link") else "")
            + f"Défi bonus (à la fin) : {p['bonus']}\nPetite question de fin : {p['check_question']}"
        )

    def on_user_message(self, text: str) -> str | None:
        les = self.lesson
        if not les:
            return None
        total = len(les["plan"]["discoveries"])
        return f"leçon : découverte {min(les['step'] + 1, total)} sur {total}"


def register_tools(llm, game: LearnGame) -> list:
    from pipecat.adapters.llm.schemas.function_schema import FunctionSchema

    suggest = game.tool(lambda a: game.suggest(), budget=True)
    start = game.tool(lambda a: game.start(a.get("topic", ""), a.get("domain", "")), budget=True)
    nxt = game.tool(lambda a: game.next(a.get("understood", ""), a.get("discovery_done")))
    end = game.tool(
        lambda a: game.end(
            a.get("retained", ""), bool(a.get("liked", True)), bool(a.get("bonus_done")), a.get("new_questions")
        )
    )

    llm.register_function("learn_suggest", suggest)
    llm.register_function("learn_start", start, cancel_on_interruption=False)  # see PREPARING_NOTE
    llm.register_function("learn_next", nxt)
    llm.register_function("learn_end", end)
    return [
        FunctionSchema(
            name="learn_suggest",
            description="Ce que l'enfant a déjà appris avec toi (domaines, niveau, sujets, centres d'intérêt, ce qu'il "
            "fait en classe, ses questions), pour lui proposer quoi découvrir.",
            properties={},
            required=[],
        ),
        FunctionSchema(
            name="learn_start",
            description="Prépare une mini-leçon de découverte sur le sujet choisi, adaptée à l'enfant."
            + PREPARING_NOTE,
            properties={
                "topic": {"type": "string", "description": "le sujet, ex. « pourquoi les volcans crachent du feu »"},
                "domain": {
                    "type": "string",
                    "description": "le domaine : sciences, histoire, maths, nature, espace, "
                    "corps humain, langues, arts, géographie…",
                },  # fmt: skip
            },
            required=["topic", "domain"],
        ),
        FunctionSchema(
            name="learn_next",
            description="Une découverte de la leçon est faite : passe à la suivante.",
            properties={
                "understood": {"type": "string", "enum": ["oui", "un peu", "non"]},
                "discovery_done": {
                    "type": "integer",
                    "description": "le numéro de la découverte qui vient d'être faite",
                },
            },
            required=["understood", "discovery_done"],
        ),
        FunctionSchema(
            name="learn_end",
            description="Fin de la leçon, après lui avoir demandé ce qu'il a retenu et si ça lui a plu.",
            properties={
                "retained": {"type": "string", "description": "ce qu'il a retenu, avec ses mots"},
                "liked": {"type": "boolean"},
                "bonus_done": {"type": "boolean", "description": "true s'il a relevé le défi bonus"},
                "new_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ses nouvelles questions, pour une prochaine fois",
                },  # fmt: skip
            },
            required=["retained", "liked", "bonus_done"],
        ),
    ]
