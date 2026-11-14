"""Technical monitoring, from the session logs: where each turn's waiting time goes, what the pet
costs, and what went wrong.

- `latency(events)`: per turn, the end-of-turn detection wait (transcript received → turn ended),
  the model's first text (TTFB) and the voice (the rest); plus medians.
- `costs(days)`: per day, the conversation model (session token metrics) and the background calls
  (plans, quizzes, condensation: logs/usage.jsonl), priced with OpenRouter's public model list;
  speech (TTS characters, STT minutes of child speech) priced only if set in config.toml [costs]
  (OpenRouter's list has no price for them).
- `errors(days)`: errors and incidents, newest first.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, datetime, timedelta

from loguru import logger

from aipet import paths, session_log

PRICES_URL = "https://openrouter.ai/api/v1/models"  # public, no key: nothing about the family is sent
PRICES_MAX_AGE_S = 24 * 3600
INCIDENTS = {
    "error": "Erreur",
    "turn_abandoned": "Tour abandonné (pas de réponse)",
    "condensation_error": "Condensation en échec",
    "condensation_given_up": "Condensation abandonnée",
    "tool_budget_exhausted": "Trop d'appels d'outils",
    "guess_win_refused": "Victoire refusée (devinette)",
    "wait_expired": "Attente de fin de phrase expirée",
}


def _sessions(days: int, today: date | None = None) -> list[tuple[str, list[dict]]]:
    today = today or datetime.now().date()
    out = []
    for n in range(days):
        d = today - timedelta(days=n)
        for p in sorted((paths.logs_dir() / "sessions").glob(f"{d:%Y-%m-%d}_*.jsonl")):
            out.append((p.name, session_log.read_session(p)))
    return out


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs)) if xs else None


# ---------------------------------------------------------------------------------------- latency
def latency(events: list[dict]) -> dict:
    """Per answered turn: wait (end-of-turn detection), llm (first text), voice (the rest), in ms."""
    turns = []
    for i, e in enumerate(events):
        if e["type"] != "bot_started_speaking" or not e.get("voice_latency_ms"):
            continue
        total = e["voice_latency_ms"]
        stop_s = e["s"] - total / 1000
        perceived = e.get("perceived_latency_ms")
        wait = perceived - total if perceived and perceived > total else 0
        llm = next(
            (
                m["value"]
                for m in events[:i]
                if m["type"] == "metric"
                and m.get("name") == "ttfb_ms"
                and "LLM" in m.get("service", "")
                and stop_s <= m["s"] <= e["s"]
            ),
            None,
        )
        stt = next(
            (
                m["value"]
                for m in reversed(events[:i])
                if m["type"] == "metric" and m.get("name") == "ttfb_ms" and "STT" in m.get("service", "")
                and m["s"] <= stop_s
            ),
            None,
        )  # fmt: skip
        text = next((m.get("text") for m in reversed(events[:i]) if m["type"] == "transcription"), "")
        turns.append({"t": e["t"][11:19], "text": text, "stt": stt, "wait": wait, "llm": llm,
                      "voice": total - (llm or 0), "total": total + wait})  # fmt: skip
    keys = ("stt", "wait", "llm", "voice", "total")
    return {"turns": turns, "median": {k: _median(t[k] for t in turns) for k in keys}}


def latency_days(days: int = 7) -> list[dict]:
    rows = []
    for name, events in _sessions(days):
        lat = latency(events)
        if lat["turns"]:
            rows.append({"session": name, "turns": len(lat["turns"]), **lat["median"]})
    return rows


# ---------------------------------------------------------------------------------------- costs
def prices(fetch: bool = True) -> dict[str, dict]:
    """OpenRouter's per-token prices by model id (USD), cached for a day."""
    cache = paths.cache_dir() / "openrouter_prices.json"
    try:
        if cache.exists() and time.time() - cache.stat().st_mtime < PRICES_MAX_AGE_S:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    if fetch:
        try:
            import httpx

            data = httpx.get(PRICES_URL, timeout=8).json()["data"]
            table = {m["id"]: m.get("pricing", {}) for m in data}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(table))
            return table
        except Exception as e:
            logger.info(f"Prices not fetched: {e}")
    try:
        return json.loads(cache.read_text())  # stale is better than nothing
    except (OSError, ValueError):
        return {}


def _price(table: dict, model: str | None, tokens_in: int, cached: int, tokens_out: int) -> float | None:
    p = table.get(model or "") or next((v for k, v in table.items() if model and k.endswith("/" + model)), None)
    if not p:  # « gemini-3.6-flash » (Google directly) = « google/gemini-3.6-flash »
        return None
    f = lambda k: float(p.get(k) or 0)  # noqa: E731
    cache_rate = f("input_cache_read") or f("prompt")
    return (tokens_in - cached) * f("prompt") + cached * cache_rate + tokens_out * f("completion")


def _conversation_model(events: list[dict]) -> str | None:
    start = next((e for e in events if e["type"] == "session_start"), {})
    conv = (start.get("config") or {}).get("engines", {}).get("conversation", "")
    parts = [p.strip() for p in conv.split("·")]
    return parts[1] if len(parts) > 1 else None


def _usage_records() -> list[dict]:
    p = paths.logs_dir() / "usage.jsonl"
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def costs(days: int = 30, cost_cfg: dict | None = None, table: dict | None = None) -> dict:
    """Per day: conversation and background model costs (USD), speech volumes (and their cost if
    [costs] gives the rates)."""
    cost_cfg = cost_cfg or {}
    table = prices() if table is None else table
    per_day: dict[str, dict] = {}

    def day(d: str) -> dict:
        return per_day.setdefault(d, {"day": d, "conversation_usd": 0.0, "background_usd": 0.0, "tokens_in": 0,
                                      "tokens_out": 0, "tts_chars": 0, "stt_minutes": 0.0, "unpriced": set()})  # fmt: skip

    for name, events in _sessions(days):
        row = day(name[:10])
        model = _conversation_model(events)
        speaking, started = 0.0, None
        for e in events:
            if e["type"] == "metric" and e.get("name") == "tokens" and "LLM" in e.get("service", ""):
                i, c, o = e.get("input") or 0, e.get("cache") or 0, (e.get("output") or 0) + (e.get("reasoning") or 0)
                row["tokens_in"] += i
                row["tokens_out"] += o
                usd = _price(table, model, i, c, o)
                if usd is None:
                    row["unpriced"].add(model or "?")
                else:
                    row["conversation_usd"] += usd
            elif e["type"] == "metric" and e.get("name") == "tts_characters":
                row["tts_chars"] += e.get("value") or 0
            elif e["type"] == "user_started_speaking":
                started = e["s"]
            elif e["type"] == "user_stopped_speaking" and started is not None:
                speaking += e["s"] - started
                started = None
        row["stt_minutes"] += speaking / 60
    since = (datetime.now().date() - timedelta(days=days - 1)).isoformat()
    for r in _usage_records():
        d = (r.get("t") or "")[:10]
        if d < since:
            continue
        row = day(d)
        row["tokens_in"] += r.get("input", 0)
        row["tokens_out"] += r.get("output", 0)
        usd = _price(table, r.get("model"), r.get("input", 0), r.get("cached", 0), r.get("output", 0))
        if usd is None:
            row["unpriced"].add(r.get("model") or "?")
        else:
            row["background_usd"] += usd
    tts_rate, stt_rate = cost_cfg.get("tts_usd_per_million_chars"), cost_cfg.get("stt_usd_per_minute")
    rows = []
    for row in sorted(per_day.values(), key=lambda r: r["day"], reverse=True):
        row["tts_usd"] = row["tts_chars"] / 1e6 * tts_rate if tts_rate else None
        row["stt_usd"] = row["stt_minutes"] * stt_rate if stt_rate else None
        row["total_usd"] = round(
            row["conversation_usd"] + row["background_usd"] + (row["tts_usd"] or 0) + (row["stt_usd"] or 0), 4
        )
        row["unpriced"] = sorted(row["unpriced"])
        for k in ("conversation_usd", "background_usd", "tts_usd", "stt_usd", "stt_minutes"):
            if row[k] is not None:
                row[k] = round(row[k], 4)
        rows.append(row)
    return {
        "days": rows,
        "speech_priced": bool(tts_rate and stt_rate),
        "prices_known": bool(table),
        "note": "Prix des modèles : liste publique d'OpenRouter (USD). Voix et transcription : tarifs à "
        "renseigner dans config.toml [costs] (absents de cette liste). Les journaux ne remontent pas aux "
        "appels de fond d'avant ce suivi.",
    }


# ---------------------------------------------------------------------------------------- errors
def errors(days: int = 14) -> list[dict]:
    out = []
    for name, events in _sessions(days):
        for e in events:
            if e["type"] in INCIDENTS:
                detail = e.get("message") or e.get("error") or e.get("reason") or ""
                if e["type"] == "guess_win_refused":
                    detail = f"« {e.get('answer')} » / enfant : « {e.get('child')} »"
                elif e["type"] == "turn_abandoned":
                    detail = f"après {e.get('after_s')} s"
                out.append({"t": e["t"][:19].replace("T", " "), "kind": INCIDENTS[e["type"]],
                            "detail": str(detail)[:300], "fatal": bool(e.get("fatal")), "session": name})  # fmt: skip
    return sorted(out, key=lambda x: x["t"], reverse=True)
