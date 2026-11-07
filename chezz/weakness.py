"""Weakness profile: your errors aggregated by phase, tactic, opening and time pressure.

Everything reads the latest finished analysis of each game and only your own moves.
`ranked` turns the profile into a list of weaknesses that drives puzzles, lessons and the daily plan.
"""
import sqlite3

from .tactics import LABELS, PUZZLE_THEME, TAGS

ERR = "m.classification IN ('inaccuracy', 'mistake', 'blunder')"
PHASES = ("opening", "middlegame", "endgame")


def _cond(where: str) -> str:
    return where.removeprefix(" WHERE ") or "1"


def profile(con: sqlite3.Connection, where: str = "", params: list | None = None) -> dict:
    params = params or []
    cond = _cond(where)
    moves_from = (
        "FROM games g JOIN latest_runs lr ON lr.game_id = g.id "
        "JOIN move_evals m ON m.run_id = lr.run_id AND m.ply > 0 AND m.color = g.user_color"
    )
    one = lambda sql: con.execute(sql, params).fetchone()
    many = lambda sql: [dict(r) for r in con.execute(sql, params)]

    s = dict(one(
        f"SELECT count(DISTINCT g.id) AS games, count(*) AS moves, sum({ERR}) AS errors, "
        f"sum(m.classification = 'inaccuracy') AS inaccuracies, sum(m.classification = 'mistake') AS mistakes, "
        f"sum(m.classification = 'blunder') AS blunders {moves_from} WHERE {cond}"
    ))
    s["accuracy"] = one(
        "SELECT avg(CASE g.user_color WHEN 'white' THEN r.white_accuracy ELSE r.black_accuracy END) "
        f"FROM games g JOIN latest_runs lr ON lr.game_id = g.id JOIN analysis_runs r ON r.id = lr.run_id "
        f"WHERE g.user_color IS NOT NULL AND {cond}"
    )[0]
    for k in ("moves", "errors", "inaccuracies", "mistakes", "blunders"):
        s[k] = s[k] or 0
    games = s["games"] or 0

    phases = {r["phase"]: r for r in many(
        f"SELECT m.phase AS phase, count(*) AS moves, avg(m.accuracy) AS accuracy, sum({ERR}) AS errors, "
        f"sum(m.classification = 'blunder') AS blunders {moves_from} WHERE {cond} GROUP BY m.phase"
    )}
    tag_rows = many(
        f"SELECT t.tag AS tag, t.kind AS kind, count(*) AS n {moves_from} "
        f"JOIN move_tags t ON t.run_id = m.run_id AND t.ply = m.ply WHERE {cond} GROUP BY t.tag, t.kind"
    )
    tactics = []
    for tag in TAGS:
        allowed = sum(r["n"] for r in tag_rows if r["tag"] == tag and r["kind"] == "allowed")
        missed = sum(r["n"] for r in tag_rows if r["tag"] == tag and r["kind"] == "missed")
        tactics.append({"tag": tag, "label": LABELS[tag], "theme": PUZZLE_THEME[tag], "allowed": allowed, "missed": missed,
                        "per_game": round((allowed + missed) / games, 2) if games else 0})

    time = {("trouble" if r["tt"] else "normal"): r for r in many(
        f"SELECT m.time_trouble AS tt, count(*) AS moves, sum({ERR}) AS errors, sum(m.classification = 'blunder') AS blunders "
        f"{moves_from} WHERE {cond} GROUP BY m.time_trouble"
    )}

    openings = many(
        "SELECT g.opening_family AS name, count(*) AS games, "
        "coalesce(sum(g.user_result = 'win'), 0) AS wins, coalesce(sum(g.user_result = 'draw'), 0) AS draws, "
        "coalesce(sum(g.user_result = 'loss'), 0) AS losses, "
        "avg(CASE g.user_color WHEN 'white' THEN r.white_accuracy ELSE r.black_accuracy END) AS accuracy, "
        "avg((SELECT count(*) FROM move_evals m WHERE m.run_id = lr.run_id AND m.color = g.user_color "
        "     AND m.classification = 'blunder')) AS blunders "
        "FROM games g LEFT JOIN latest_runs lr ON lr.game_id = g.id LEFT JOIN analysis_runs r ON r.id = lr.run_id "
        f"WHERE g.user_color IS NOT NULL AND g.opening_family IS NOT NULL AND {cond} "
        "GROUP BY g.opening_family ORDER BY count(*) DESC LIMIT 15"
    )
    for o in openings:
        o["score"] = round(100 * (o["wins"] + o["draws"] / 2) / o["games"]) if o["games"] else None

    result = {
        "summary": s,
        "phases": [dict(phases.get(p, {"phase": p, "moves": 0, "accuracy": None, "errors": 0, "blunders": 0}), phase=p) for p in PHASES],
        "tactics": tactics,
        "time": {k: time.get(k, {"moves": 0, "errors": 0, "blunders": 0}) for k in ("normal", "trouble")},
        "openings": openings,
    }
    result["weaknesses"] = ranked(result)
    return result


def ranked(p: dict, limit: int = 8) -> list[dict]:
    """Weaknesses sorted by cost in 'errors per game', the common unit across kinds."""
    s = p["summary"]
    games, moves = s["games"] or 0, s["moves"] or 0
    if not games or not moves:
        return []
    overall = s["errors"] / moves
    items = []
    for t in p["tactics"]:
        n = t["allowed"] + t["missed"]
        if n:
            items.append({"key": f"tag:{t['tag']}", "kind": "tactic", "tag": t["tag"], "theme": t["theme"], "label": t["label"],
                          "score": round(n / games, 2),
                          "detail": f"{t['allowed']} allowed, {t['missed']} missed in {games} game{'s' if games > 1 else ''}"})

    def excess(label: str, key: str, kind: str, row: dict, **extra):
        if row["moves"] >= 10:
            extra_errors = (row["errors"] - overall * row["moves"]) / games
            rate = row["errors"] / row["moves"]
            if extra_errors > 0.05:
                items.append({"key": key, "kind": kind, "label": label, "score": round(extra_errors, 2),
                              "detail": f"{100 * rate:.0f} errors per 100 moves vs {100 * overall:.0f} overall", **extra})

    for ph in p["phases"]:
        excess(f"{ph['phase'].title()} play", f"phase:{ph['phase']}", "phase", ph, phase=ph["phase"])
    excess("Time pressure", "time", "time", p["time"]["trouble"])
    for o in p["openings"]:
        if o["games"] >= 3 and o["score"] is not None and o["score"] < 40:
            items.append({"key": f"opening:{o['name']}", "kind": "opening", "label": o["name"], "opening": o["name"],
                          "score": round((50 - o["score"]) / 50, 2), "detail": f"{o['score']}% score in {o['games']} games"})
    items.sort(key=lambda i: -i["score"])
    return items[:limit]
