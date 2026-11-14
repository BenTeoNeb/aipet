"""Statistics on the pet's session logs (free: no model call, reads local logs only).

uv run analyze.py                # the last 7 days
uv run analyze.py --days 30
uv run analyze.py --session 2026-11-11_175900_web.jsonl   # timeline of one session
"""

import argparse
import json
import statistics
import time
from collections import Counter

from aipet import session_log as sl

# Thresholds for the [attends] tag (unfinished sentence): it must stay rare and justified.
MAX_WAIT_RATE = 0.15  # waits per pet turn
MAX_EXPIRED_WAIT_RATE = 0.50  # share of waits where the child had actually finished


def pct(values, p):
    v = sorted(values)
    return v[min(len(v) - 1, int(len(v) * p))] if v else None


def double_replies(events: list[dict]) -> list[dict]:
    """Two non-empty model replies with no transcription between them, classified by cause:
    - "after tools": a tool was called during that child turn (since its last transcription);
    - "after interruption": the pet was cut off while speaking shortly before the first reply;
    - "other"."""
    speaking, cut_at, prev = False, [], None
    out = []
    for i, e in enumerate(events):
        t = e["type"]
        if t == "bot_started_speaking":
            speaking = True
        elif t == "bot_stopped_speaking":
            speaking = False
        elif t == "interruption" and speaking:
            cut_at.append(e["s"])
        if t != "llm_response" or not (e.get("raw_text") or "").strip():
            continue
        if prev is not None and not any(x["type"] == "transcription" for x in events[prev + 1 : i]):
            turn_start = max(j for j in range(prev + 1) if j == 0 or events[j]["type"] == "transcription")
            turn = events[turn_start:i]
            first_s = events[prev]["s"]
            cause = (
                "after tools"
                if any(x["type"] in ("tool_call", "tool_result") for x in turn)
                else "after interruption"
                if any(first_s - 2 <= c <= e["s"] for c in cut_at)
                else "other"
            )
            out.append(
                {"s": e["s"], "cause": cause, "first": events[prev]["raw_text"][:70], "second": e["raw_text"][:70]}
            )
        prev = i
    return out


def usage_by_day(events: list[dict]) -> dict[str, dict]:
    """Child turns per day and per speaker (from `turn` events), naps and sleepy replies."""
    out: dict[str, dict] = {}
    for e in events:
        day = (e.get("t") or "")[:10]
        if not day:
            continue
        u = out.setdefault(day, {"turns": Counter(), "naps": 0, "sleepy": 0})
        if e["type"] == "turn":
            u["turns"][e.get("speaker") or "unknown"] += 1
        elif e["type"] == "fell_asleep":
            u["naps"] += 1
        elif e["type"] == "sleepy_reply":
            u["sleepy"] += 1
    return {d: u for d, u in out.items() if u["turns"] or u["naps"] or u["sleepy"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--session", help="session file name, with or without .jsonl; « last » for the latest")
    a = ap.parse_args()

    if a.session:
        if a.session == "last":
            path = max(sl.SESSIONS.glob("*.jsonl"), key=lambda f: f.name)
        else:
            path = sl.SESSIONS / (a.session if a.session.endswith(".jsonl") else f"{a.session}.jsonl")
        for e in sl.read_session(path):
            if e["type"] != "metric":
                d = {k: v for k, v in e.items() if k not in ("t", "s", "type")}
                print(f"{e['s']:8.1f}s  {e['type']:<22} {json.dumps(d, ensure_ascii=False)[:160]}")
        return

    since = time.time() - a.days * 86400
    files = [p for p in sorted(sl.SESSIONS.glob("*.jsonl")) if p.stat().st_mtime >= since]
    if not files:
        print(f"No session in the last {a.days} days ({sl.SESSIONS}).")
        return
    lat, ttfb, tools, errors, emotions = [], {}, Counter(), Counter(), Counter()
    turns = tok_in = tok_out = duration = waits = waits_useful = waits_expired = 0
    doubles = []
    for p in files:
        ev = sl.read_session(p)
        s = sl.summarize(ev)
        turns += s["turns"]
        tok_in += s["tokens_in"]
        tok_out += s["tokens_out"]
        duration += s["duration_s"] or 0
        waits += s["waits"]
        waits_useful += s["waits_useful"]
        waits_expired += s["waits_expired"]
        doubles += [{"session": p.name, **d} for d in double_replies(ev)]
        for e in ev:
            t = e["type"]
            if t == "bot_started_speaking" and e.get("voice_latency_ms"):
                lat.append(e["voice_latency_ms"])
            elif t == "metric" and e.get("name") == "ttfb_ms":
                ttfb.setdefault(e["service"].split("#")[0], []).append(e["value"])
            elif t == "tool_call":
                tools[e["name"]] += 1
            elif t in ("error", "condensation_error"):
                errors[(e.get("message") or e.get("error") or "?")[:80]] += 1
            elif t == "emotion":
                emotions[e["emotion"]] += 1

    print(f"=== {len(files)} sessions over {a.days} days · {duration / 60:.0f} min · {turns} turns ===")
    if lat:
        print(
            f"Latency end of speech → voice: median {statistics.median(lat):.0f} ms · "
            f"p90 {pct(lat, 0.9)} ms · max {max(lat)} ms"
        )
    for svc, v in sorted(ttfb.items()):
        print(f"TTFB {svc:<28} median {statistics.median(v):.0f} ms · p90 {pct(v, 0.9)} ms (n={len(v)})")
    print(f"Tokens: {tok_in} in, {tok_out} out")
    if tools:
        print("Tools:", ", ".join(f"{k} ×{n}" for k, n in tools.most_common()))
    if emotions:
        print("Emotions:", ", ".join(f"{k} ×{n}" for k, n in emotions.most_common()))

    rate = waits / turns if turns else 0
    print(f"Unfinished-sentence waits: {waits} ({rate:.0%} of turns) · useful {waits_useful} · expired {waits_expired}")
    if turns and rate > MAX_WAIT_RATE:
        print(
            f"  ⚠️  more than {MAX_WAIT_RATE:.0%} of turns wait: shorten the delay or disable "
            f"[listening].wait_for_unfinished_sentence"
        )
    if waits and waits_expired / waits > MAX_EXPIRED_WAIT_RATE:
        print(f"  ⚠️  {waits_expired / waits:.0%} of waits expired: the child had usually finished")

    print(
        f"Double replies: {len(doubles)}"
        + (
            " (" + ", ".join(f"{c} ×{n}" for c, n in Counter(d["cause"] for d in doubles).most_common()) + ")"
            if doubles
            else ""
        )
    )
    for d in doubles[:10]:
        print(f"  {d['session']} {d['s']:.0f}s [{d['cause']}] {d['first']!r} || {d['second']!r}")

    usage = usage_by_day([e for f in files for e in sl.read_session(f)])
    if usage:
        print("Usage per day (child turns · naps · sleepy replies):")
        for day, u in sorted(usage.items()):
            kids = ", ".join(f"{k} ×{n}" for k, n in u["turns"].most_common()) or "-"
            print(f"  {day}: {kids} · naps {u['naps']} · sleepy replies {u['sleepy']}")
    print("Errors:", "none" if not errors else "")
    for msg, n in errors.most_common(10):
        print(f"  ×{n}  {msg}")


if __name__ == "__main__":
    main()
