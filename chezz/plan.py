"""Today's plan, streak and long-term progress data."""
import sqlite3
from datetime import date, timedelta

from . import lessons, openings, puzzles, weakness
from .tactics import LABELS, TAGS

PLAN_PUZZLES = 5
DEFAULT_THEMES = ["fork", "hangingPiece", "mateIn2"]


def streak(con: sqlite3.Connection) -> dict:
    days = sorted({date.fromisoformat(r[0]) for r in con.execute("SELECT day FROM activity WHERE count > 0")}, reverse=True)
    today = date.today()
    active = set(days)
    cur = 0
    d = today if today in active else today - timedelta(days=1)
    while d in active:
        cur += 1
        d -= timedelta(days=1)
    best = run = 0
    prev = None
    for d in sorted(active):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    recent = {r[0]: r[1] for r in con.execute(
        "SELECT day, sum(count) FROM activity WHERE day >= date('now', 'localtime', '-83 days') GROUP BY day")}
    return {"current": cur, "best": best, "today": today in active,
            "days": [{"day": (today - timedelta(days=i)).isoformat(), "count": recent.get((today - timedelta(days=i)).isoformat(), 0)}
                     for i in range(83, -1, -1)]}


def plan_themes(weak: list[dict]) -> list[str]:
    themes = [w["theme"] for w in weak if w.get("theme")]
    for w in weak:
        if w["kind"] == "phase" and w["phase"] in ("opening", "endgame"):
            themes.append(w["phase"])
    return themes[:4] or DEFAULT_THEMES


def game_to_review(con: sqlite3.Connection) -> dict | None:
    """Recent analysed game you haven't reviewed, with the most mistakes; else the latest unanalysed one."""
    row = con.execute(
        "SELECT g.id, g.white, g.black, g.date, g.user_result, g.opening, "
        "  (SELECT count(*) FROM move_evals m WHERE m.run_id = lr.run_id AND m.color = g.user_color "
        "   AND m.classification IN ('mistake', 'blunder')) AS errors "
        "FROM games g JOIN latest_runs lr ON lr.game_id = g.id "
        "WHERE g.user_color IS NOT NULL AND g.reviewed_at IS NULL "
        "AND g.id IN (SELECT id FROM games WHERE user_color IS NOT NULL ORDER BY date DESC, id DESC LIMIT 20) "
        "ORDER BY errors DESC, g.date DESC LIMIT 1"
    ).fetchone()
    if row:
        return dict(row, analysed=True)
    row = con.execute(
        "SELECT id, white, black, date, user_result, opening FROM games g WHERE NOT EXISTS "
        "(SELECT 1 FROM latest_runs lr WHERE lr.game_id = g.id) "
        "ORDER BY g.user_color IS NULL, date DESC, id DESC LIMIT 1"  # your own games first
    ).fetchone()
    return dict(row, analysed=False, errors=None) if row else None


def today(con: sqlite3.Connection, lichess_path) -> dict:
    prof = weakness.profile(con)
    weak = prof["weaknesses"]
    ps = puzzles.summary(con, lichess_path)
    rating_trend = [r[0] for r in con.execute(
        "SELECT rating_after FROM (SELECT rating_after, id FROM puzzle_attempts WHERE rated = 1 ORDER BY id DESC LIMIT 30) ORDER BY id")]
    acc_trend = [r[0] for r in con.execute(
        "SELECT acc FROM (SELECT CASE g.user_color WHEN 'white' THEN r.white_accuracy ELSE r.black_accuracy END AS acc, g.date, g.id "
        "FROM games g JOIN latest_runs lr ON lr.game_id = g.id JOIN analysis_runs r ON r.id = lr.run_id "
        "WHERE g.user_color IS NOT NULL ORDER BY g.date DESC, g.id DESC LIMIT 20) ORDER BY date, id")]
    return {
        "games": con.execute("SELECT count(*) FROM games").fetchone()[0],
        "puzzles": {"target": PLAN_PUZZLES, "done": min(ps["today"], PLAN_PUZZLES), "themes": plan_themes(weak),
                    "due": ps["due"], "own_unseen": ps["own_unseen"], "lichess": ps["lichess_count"] > 0},
        "lesson": lessons.recommended(con, weak),
        "game": game_to_review(con),
        "openings_due": openings.due_cards(con),
        "streak": streak(con),
        "rating": {"current": ps["rating"], "trend": rating_trend},
        "accuracy_trend": acc_trend,
        "weaknesses": weak[:3],
    }


def progress(con: sqlite3.Connection) -> dict:
    games = [dict(r) for r in con.execute(
        "SELECT g.id, g.date, g.white, g.black, g.user_color, g.user_result, "
        "CASE g.user_color WHEN 'white' THEN r.white_accuracy ELSE r.black_accuracy END AS accuracy, "
        "(SELECT count(*) FROM move_evals m WHERE m.run_id = lr.run_id AND m.color = g.user_color AND m.classification = 'blunder') AS blunders, "
        "(SELECT count(*) FROM move_evals m WHERE m.run_id = lr.run_id AND m.color = g.user_color AND m.classification = 'mistake') AS mistakes "
        "FROM games g JOIN latest_runs lr ON lr.game_id = g.id JOIN analysis_runs r ON r.id = lr.run_id "
        "WHERE g.user_color IS NOT NULL ORDER BY g.date, g.id")]
    rating = [dict(r) for r in con.execute(
        "SELECT created_at AS at, rating_after AS rating FROM puzzle_attempts WHERE rated = 1 ORDER BY id")]
    months: dict[str, dict] = {}
    for r in con.execute(
        "SELECT substr(g.date, 1, 7) AS month, count(*) AS errors FROM games g JOIN latest_runs lr ON lr.game_id = g.id "
        "JOIN move_evals m ON m.run_id = lr.run_id AND m.color = g.user_color "
        "WHERE m.classification IN ('inaccuracy', 'mistake', 'blunder') AND g.date IS NOT NULL GROUP BY 1 ORDER BY 1"):
        months[r["month"]] = {"month": r["month"], "errors": r["errors"], "shares": {}}
    for r in con.execute(
        "SELECT substr(g.date, 1, 7) AS month, t.tag, count(DISTINCT t.ply) AS n FROM games g "
        "JOIN latest_runs lr ON lr.game_id = g.id JOIN move_evals m ON m.run_id = lr.run_id AND m.color = g.user_color "
        "JOIN move_tags t ON t.run_id = m.run_id AND t.ply = m.ply WHERE g.date IS NOT NULL GROUP BY 1, 2"):
        if r["month"] in months:
            months[r["month"]]["shares"][r["tag"]] = round(100 * r["n"] / months[r["month"]]["errors"], 1)
    one = lambda sql: con.execute(sql).fetchone()[0]
    return {
        "games": games,
        "rating": rating,
        "weakness_months": list(months.values()),
        "tag_labels": {t: LABELS[t] for t in TAGS},
        "streak": streak(con),
        "totals": {
            "games": one("SELECT count(*) FROM games"),
            "analysed": one("SELECT count(*) FROM latest_runs"),
            "puzzles_solved": one("SELECT count(*) FROM puzzle_attempts WHERE solved = 1"),
            "lessons_done": one("SELECT count(*) FROM lesson_progress WHERE completed_at IS NOT NULL"),
            "drills": one("SELECT coalesce(sum(count), 0) FROM activity WHERE kind = 'drill'"),
        },
    }
