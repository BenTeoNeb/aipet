"""A simulated child plays an adventure against the real narrator (`pet-playtest`).

The narrator is the pet's own: its base prompt, the adventure activity's instructions, the adventure
tools and the engine, with the conversation model (text instead of voice). The child is another
model call, told to play like a child of that age: short spoken sentences, clumsy words, wrong
guesses, help when stuck. A judge reads every narrator turn against what the engine answered.

What it finds, per turn: the engine's answers that say the world doesn't know what the child meant
(free, absent, impossible: missing aliases or triggers), and the narrator's slips (telling something
the engine didn't say, giving the solution away, inventing a clue). Each game is written as a session
log in `logs/playtests/` (the explorer page shows them under « Parties »).

    uv run pet-playtest "Le phare de Piko"                      # a bank adventure
    uv run pet-playtest docs/adventure/model/le-phare-de-piko.json --child "Léo, 5 ans" --turns 25
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from openai import OpenAI

from aipet import engines, paths
from aipet.config import load_config
from aipet.games import adventure as adv
from aipet.games.adventure_engine import State, World
from aipet.games.common import Host
from aipet.prompts import build_system_prompt
from aipet.timefmt import now

SKILL = Path(__file__).parent.parent / "builtin_skills" / "adventure" / "SKILL.md"
NARRATOR_STEPS = 8  # model calls per child turn (tool calls, then the spoken answer)
TAGS = re.compile(r"\[[^\]]{1,30}\]\s*")

# Model-facing (French).
CHILD_PROMPT = """Tu joues le rôle de {child}, qui joue à une aventure à voix haute avec {pet}, son animal
de compagnie qui parle. {pet} raconte ; toi, tu es le héros et tu dis ce que tu fais.

Parle comme un vrai enfant de cet âge, à l'oral : des phrases courtes, parfois maladroites (« euh… »,
« je prends le truc là »), des mots d'enfant au lieu des mots exacts, parfois plusieurs choses d'un coup.
{style}
Tu veux gagner, mais tu ne connais pas la solution : tu explores, tu essaies des idées (des bonnes et des
mauvaises), tu te trompes parfois à une devinette ou un code, tu oublies des détails ; si tu es bloqué
depuis un moment, tu demandes de l'aide. Quand l'aventure est finie, tu réponds à la question de {pet},
puis tu dis au revoir.

Réponds UNIQUEMENT ce que tu dis à voix haute, en une ou deux phrases, sans guillemets ni didascalies."""

STYLES = {
    "curieux": "Tu es curieux : tu regardes tout, tu poses des questions aux personnages.",
    "pressé": "Tu es pressé : tu fonces, tu essaies des choses sans trop regarder.",
    "rêveur": "Tu es rêveur : tu inventes parfois des choses qui ne sont pas dans l'histoire (ton doudou, un dragon).",
    "limites": "Tu testes les limites, exprès : tu effaces ou casses des choses, tu jettes des objets (même utiles), "
    "tu demandes où sont les choses, tu ramasses des choses qui ne sont pas dans l'histoire (cailloux, bâton), tu "
    "fouilles ton sac, tu donnes des surnoms aux personnages et tu vérifies plus tard qu'on s'en souvient.",
}

JUDGE_PROMPT = """Tu vérifies le maître du jeu d'une aventure pour enfants. Un programme tient la vérité du
monde ; le maître du jeu (un modèle de langage) doit raconter ce que le programme a répondu, sans rien
inventer qui change le jeu, et guider sans donner la solution.

On te donne : ce que l'enfant a dit, ce que le programme a répondu (outcomes, textes, l'état), et ce que le
maître du jeu a raconté. Relève seulement les vrais problèmes :
- contradictions : il raconte quelque chose de faux par rapport au programme (une porte fermée qui est
  ouverte, un objet gagné qui ne l'est pas, une action réussie qui a échoué, un lieu où il n'est pas) ;
- solution_donnee : il souffle l'action qui résout une énigme alors que l'enfant ne l'a pas demandée et
  qu'aucun indice du programme ne la donne (un indice du programme, au niveau donné, n'est pas une faute) ;
- piste_inventee : il invente une piste ou un conseil de jeu que le programme n'a pas donné et qui
  pourrait égarer l'enfant ;
- sans_appel : l'enfant a fait une action dans l'aventure, mais le maître du jeu ne l'a pas fait passer
  par le programme (aucun appel) et l'a racontée quand même ;
- a_savoir_oublie : le programme a donné un « à_savoir » (une porte fermée à clé, un code…) et le maître
  du jeu ne l'a pas dit, même avec ses mots.
Ne sont PAS des fautes : les embellissements (bruitages, émotions, détails de décor sans effet) ;
confirmer ou reformuler ce que l'enfant propose lui-même ; répéter ce qu'un personnage ou le programme a
déjà dit (même à un tour précédent) ; proposer de prendre un objet qui vient d'apparaître ; lister les
chemins et les choses qu'on voit."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "contradictions": {"type": "array", "items": {"type": "string"}},
        "solution_donnee": {"type": "string", "description": "la phrase fautive, ou vide"},
        "piste_inventee": {"type": "string", "description": "la phrase fautive, ou vide"},
        "sans_appel": {"type": "string", "description": "l'action racontée sans appel, ou vide"},
        "a_savoir_oublie": {"type": "string", "description": "l'« à_savoir » passé sous silence, ou vide"},
    },
    "required": ["contradictions", "solution_donnee", "piste_inventee", "sans_appel", "a_savoir_oublie"],
}
UNKNOWN = {"free", "absent", "impossible"}  # the world didn't know what the child meant


class Child(Host):
    def __init__(self, name: str):
        self.name = name
        self.skills = type("S", (), {"active": "adventure"})()

    def player(self):
        return self.name

    def next_offer(self):
        return ""


class _NoLLM:
    def register_function(self, *a, **k):
        pass


def skill_section() -> str:
    body = SKILL.read_text(encoding="utf-8").split("---", 2)[2]
    return ("\n=== ACTIVITÉ EN COURS : adventure ===\nCes consignes complètent tes règles et ne les remplacent "
            f"jamais : en cas de conflit, tes règles et tes garde-fous gagnent toujours.\n{body}\n"
            "=== FIN DE L'ACTIVITÉ adventure ===\n")  # fmt: skip


def tool_specs(game) -> list[dict]:
    schemas = adv.register_tools(_NoLLM(), game)
    out = [{"type": "function", "function": {"name": s.name, "description": s.description, "parameters": {
        "type": "object", "properties": s.properties, "required": s.required}}} for s in schemas]  # fmt: skip
    out.append({"type": "function", "function": {"name": "end_skill", "description": "Termine l'activité en cours.",
                                                  "parameters": {"type": "object", "properties": {}}}})  # fmt: skip
    return out


async def run_tool(game, name: str, args: dict) -> dict:
    if name == "adventure_start":
        return await game.start(
            args.get("idea", ""), args.get("difficulty", ""), bool(args.get("resume")), args.get("replay", ""),
            bool(args.get("from_checkpoint")), bool(args.get("new")), args.get("title", ""),
        )  # fmt: skip
    if name == "adventure_act":
        return game.act(args.get("actions"), args.get("said", ""), args.get("remember", ""))
    if name == "adventure_end":
        return game.end(args.get("reaction", ""))
    return {"ok": True}


def spoken(text: str) -> str:
    return TAGS.sub("", text or "").replace("*", "").strip()


def state_line(w: World, st: State) -> str:
    opened = [w.name(i, st) for i in w.items if w.prop(st, i, "open") and w.items[i].get("openable")]
    return (f"lieu : {w.name(st.place)} ; sac : {', '.join(w.name(i, st) for i in st.inventory) or 'vide'} ; "
            f"ouvert : {', '.join(opened) or 'rien'} ; fin : {st.ending or 'non'}")  # fmt: skip


class Playtest:
    def __init__(self, cfg: dict, graph: dict, child: str, style: str, log: Path):
        self.cfg, self.graph, self.child = cfg, graph, child
        self.style = STYLES.get(style, style)
        self.log = log
        self.mem = Path(tempfile.mkdtemp(prefix="playtest-"))  # never the family's memory
        self.game = adv.AdventureGame(cfg, self.mem, Child(child.split(",")[0].strip()))
        r = engines.settings(cfg, "conversation")
        self.model, self.body = r["model"], engines._openrouter_body(r, "conversation")
        self.client = OpenAI(api_key=engines._api_key(r, "openrouter"), base_url=r.get("url") or engines.OPENROUTER_URL)
        self.narrator: list[dict] = []
        self.kid: list[dict] = []
        self.issues: list[dict] = []
        self.unknown: list[dict] = []
        self.ending: str | None = None

    def ev(self, type_: str, **data):
        with self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now().isoformat(timespec="seconds"), "type": type_, **data}, ensure_ascii=False))
            f.write("\n")

    def system(self) -> str:
        return "\n\n".join([build_system_prompt(self.cfg), skill_section(), self.game.prompt_section()])

    def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None):
        sent: Any = [{"role": "system", "content": system}, *messages]
        extra: Any = {"tools": tools} if tools else {}
        return (
            self.client.chat.completions.create(
                model=self.model, messages=sent, max_tokens=engines.MAX_TOKENS, extra_body=self.body, **extra
            )
            .choices[0]
            .message
        )

    async def narrate(self, said: str) -> tuple[str, list[dict]]:
        """One child turn through the narrator: (what it says, the engine's answers)."""
        self.narrator.append({"role": "user", "content": said})
        answers: list[dict] = []
        for _ in range(NARRATOR_STEPS):
            m = self.chat(self.system(), self.narrator, tool_specs(self.game))
            calls = m.tool_calls or []
            self.narrator.append({"role": "assistant", "content": m.content or "",
                                  **({"tool_calls": [c.model_dump() for c in calls]} if calls else {})})  # fmt: skip
            if not calls:
                return m.content or "", answers
            for c in calls:
                args = json.loads(c.function.arguments or "{}")
                self.ev("tool_call", name=c.function.name, arguments=args)
                res = await run_tool(self.game, c.function.name, args)
                self.ev("tool_result", name=c.function.name, result=res)
                answers.append({"tool": c.function.name, "arguments": args, "result": res})
                self.narrator.append(
                    {"role": "tool", "tool_call_id": c.id, "content": json.dumps(res, ensure_ascii=False)}
                )
        return "", answers

    def speak_as_child(self) -> str:
        system = CHILD_PROMPT.format(child=self.child, pet=getattr(self.cfg.get("pet"), "name", "Flocon"),
                                     style=self.style)  # fmt: skip
        return spoken(self.chat(system, self.kid).content or "…")

    def judge(self, said: str, answers: list[dict], told: str) -> dict:
        w, st = self.game.world, self.game.state
        prompt = (f"L'enfant a dit : « {said} »\n\nLe programme a répondu :\n"
                  + ("\n".join(json.dumps(a, ensure_ascii=False)[:1500] for a in answers) or "(aucun appel)")
                  + (f"\n\nÉtat après : {state_line(w, st)}" if w and st else "")
                  + f"\n\nLe maître du jeu a raconté :\n{told}")  # fmt: skip
        try:
            return engines.complete_json(self.cfg, JUDGE_PROMPT, prompt, JUDGE_SCHEMA, role="preparation",
                                         activity="playtest", retries=0)  # fmt: skip
        except Exception as e:  # the judge is a help, not the test
            return {"error": type(e).__name__}

    def note(self, turn: int, said: str, answers: list[dict], verdict: dict):
        for a in answers:
            for r in (a["result"].get("results") or []) if isinstance(a["result"], dict) else []:
                if r.get("outcome") in UNKNOWN:
                    intent = a["arguments"].get("actions")
                    self.unknown.append({"turn": turn, "said": said, "outcome": r["outcome"], "intents": intent})
        found = [("contradiction", x) for x in verdict.get("contradictions") or []]
        found += [
            (k, verdict[k])
            for k in ("solution_donnee", "piste_inventee", "sans_appel", "a_savoir_oublie")
            if verdict.get(k)
        ]
        for kind, text in found:
            self.issues.append({"turn": turn, "kind": kind, "text": text})
            self.ev("playtest_issue", kind=kind, text=text)

    async def play(self, opening: str, turns: int) -> dict:
        title = self.graph["title"]
        self.ev("playtest_start", title=title, child=self.child, style=self.style)
        said = opening
        for turn in range(1, turns + 1):
            self.ev("playtest_child", text=said)
            print(f"\n👧 {said}")
            told, answers = await self.narrate(said)
            for a in answers:
                outs = (
                    [r.get("outcome") for r in a["result"].get("results", [])] if isinstance(a["result"], dict) else []
                )
                print(
                    f"   ⚙ {a['tool']} {json.dumps(a['arguments'].get('actions', a['arguments']), ensure_ascii=False)[:160]} → {outs}"
                )
            print(f"🐱 {spoken(told)}")
            self.ev("playtest_narrator", text=told)
            verdict = self.judge(said, answers, told)
            self.note(turn, said, answers, verdict)
            for i in self.issues:
                if i["turn"] == turn:
                    print(f"   ⚠ {i['kind']} : {i['text']}")
            st = self.game.state
            if st and st.ending:
                self.ending = st.ending
            if any(a["tool"] in ("adventure_end", "end_skill") for a in answers) or (
                st and st.ending and turn > 1 and self.kid and "revoir" in said
            ):
                break
            self.kid += [{"role": "assistant", "content": said}, {"role": "user", "content": spoken(told)}]
            said = self.speak_as_child()
        report = {"title": title, "child": self.child, "turns": turn, "ending": self.ending,
                  "unknown": self.unknown, "issues": self.issues, "log": str(self.log)}  # fmt: skip
        self.ev("playtest_end", **{k: v for k, v in report.items() if k != "log"})
        return report


def load_graph(what: str) -> dict:
    """A bank title, or a JSON file (a reference adventure is played under its explorer-page title)."""
    p = Path(what)
    if p.suffix == ".json" and p.exists():
        g = json.loads(p.read_text(encoding="utf-8"))
        if p.parent.resolve() == (paths.ROOT / "docs/adventure/model").resolve():
            g["title"] = f"{g['title']} (nouvelle version)"
        return g
    found = [g for g in adv.library() if what.lower() in g["title"].lower()]
    if not found:
        raise SystemExit(f"no adventure « {what} »: {', '.join(g['title'] for g in adv.library())}")
    return found[0]


def main():
    ap = argparse.ArgumentParser(description="A simulated child plays an adventure against the real narrator.")
    ap.add_argument("adventure", help="a bank title (or part of it), or a JSON file")
    ap.add_argument("--child", default="Zina, 7 ans")
    ap.add_argument("--style", default="curieux", help=f"{', '.join(STYLES)}, or your own words")
    ap.add_argument("--turns", type=int, default=30)
    args = ap.parse_args()
    cfg = load_config()
    graph = load_graph(args.adventure)
    adv.library = lambda difficulty=None: [g for g in [graph] if difficulty in (None, g["difficulty"])]
    out = paths.logs_dir() / "playtests"
    out.mkdir(parents=True, exist_ok=True)
    log = out / f"{now().strftime('%Y-%m-%d_%H%M%S')}_playtest.jsonl"
    test = Playtest(cfg, graph, args.child, args.style, log)
    opening = f"On fait une aventure ? « {graph['title'].split(' · ')[0]} », en {graph['difficulty']} !"
    report = asyncio.run(test.play(opening, args.turns))
    print(
        f"\n=== {report['title']} · {report['child']} · {report['turns']} tours · fin : {report['ending'] or 'aucune'}"
    )
    print(f"Ce que le monde n'a pas compris ({len(report['unknown'])}) :")
    for u in report["unknown"]:
        print(
            f"  tour {u['turn']} « {u['said']} » → {u['outcome']} {json.dumps(u['intents'], ensure_ascii=False)[:140]}"
        )
    print(f"Écarts du narrateur ({len(report['issues'])}) :")
    for i in report["issues"]:
        print(f"  tour {i['turn']} {i['kind']} : {i['text']}")
    print(f"Journal : {log}")


if __name__ == "__main__":
    main()
