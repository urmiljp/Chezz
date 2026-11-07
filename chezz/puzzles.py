"""Puzzles: own-blunder generation, Lichess DB import, selection, Glicko-lite rating, Leitner reviews.

All puzzles use the Lichess format: `fen` is the position before the opponent's last move, `moves[0]`
is that move (played automatically), then solver and opponent moves alternate.
"""
import csv
import io
import math
import random
import sqlite3
import threading
from pathlib import Path

import chess
import chess.engine

from . import db, review
from .tactics import PUZZLE_THEME

START_RATING, START_RD, MIN_RD, MAX_RD = 1500.0, 350.0, 60.0, 350.0
LEITNER_DAYS = [1, 3, 7, 14, 30]  # box 1..5; passing box 5 retires the card
# Themes indexed from the Lichess DB (the ones lessons, weaknesses and the filter use).
THEMES = [
    "hangingPiece", "fork", "pin", "skewer", "discoveredAttack", "backRankMate", "mate", "mateIn1", "mateIn2",
    "mateIn3", "sacrifice", "deflection", "attraction", "trappedPiece", "doubleCheck", "promotion",
    "opening", "middlegame", "endgame", "pawnEndgame", "rookEndgame", "queenEndgame", "kingsideAttack", "defensiveMove",
]
THEME_LABELS = {
    "hangingPiece": "Hanging piece", "fork": "Fork", "pin": "Pin", "skewer": "Skewer",
    "discoveredAttack": "Discovered attack", "backRankMate": "Back-rank mate", "mate": "Checkmate",
    "mateIn1": "Mate in 1", "mateIn2": "Mate in 2", "mateIn3": "Mate in 3", "sacrifice": "Sacrifice",
    "deflection": "Deflection", "attraction": "Attraction", "trappedPiece": "Trapped piece",
    "doubleCheck": "Double check", "promotion": "Promotion", "opening": "Opening", "middlegame": "Middlegame",
    "endgame": "Endgame", "pawnEndgame": "Pawn endgame", "rookEndgame": "Rook endgame",
    "queenEndgame": "Queen endgame", "kingsideAttack": "Kingside attack", "defensiveMove": "Defensive move",
}


# ---------- rating + spaced repetition ----------

_Q = math.log(10) / 400


def _g(rd: float) -> float:
    return 1 / math.sqrt(1 + 3 * _Q * _Q * rd * rd / math.pi ** 2)


def glicko(r: float, rd: float, opp_r: float, opp_rd: float, score: float) -> tuple[float, float]:
    """One-game Glicko-1 update (no time decay; RD floored so the rating keeps moving)."""
    g = _g(opp_rd)
    e = 1 / (1 + 10 ** (-g * (r - opp_r) / 400))
    d2 = 1 / (_Q * _Q * g * g * e * (1 - e))
    denom = 1 / (rd * rd) + 1 / d2
    return r + (_Q / denom) * g * (score - e), max(MIN_RD, min(MAX_RD, math.sqrt(1 / denom)))


def leitner(box: int | None, solved: bool) -> tuple[int | None, int | None]:
    """Next (box, days until due). (None, None) = not in review (never failed, or retired)."""
    if not solved:
        return 1, LEITNER_DAYS[0]
    if box is None:
        return None, None
    if box >= len(LEITNER_DAYS):
        return None, None
    return box + 1, LEITNER_DAYS[box]


def player_rating(con: sqlite3.Connection) -> tuple[float, float]:
    return float(db.kv_get(con, "puzzle_rating", START_RATING)), float(db.kv_get(con, "puzzle_rd", START_RD))


def record_attempt(con: sqlite3.Connection, puzzle_id: int, solved: bool, hints: int, seconds: float | None) -> dict:
    p = con.execute("SELECT * FROM puzzles WHERE id = ?", (puzzle_id,)).fetchone()
    if p is None:
        raise KeyError(puzzle_id)
    first = con.execute("SELECT count(*) FROM puzzle_attempts WHERE puzzle_id = ?", (puzzle_id,)).fetchone()[0] == 0
    rated = first and p["source"] == "lichess"
    r, rd = player_rating(con)
    new_r, new_rd = r, rd
    if rated:
        new_r, new_rd = glicko(r, rd, p["rating"], 75, 1.0 if solved else 0.0)
        db.kv_set(con, "puzzle_rating", round(new_r, 1))
        db.kv_set(con, "puzzle_rd", round(new_rd, 1))
    con.execute(
        "INSERT INTO puzzle_attempts (puzzle_id, solved, hints, seconds, rated, rating_after) VALUES (?, ?, ?, ?, ?, ?)",
        (puzzle_id, int(solved), hints, seconds, int(rated), round(new_r, 1)),
    )
    cur = con.execute("SELECT box FROM puzzle_reviews WHERE puzzle_id = ?", (puzzle_id,)).fetchone()
    box, days = leitner(cur["box"] if cur else None, solved)
    if box is None:
        con.execute("DELETE FROM puzzle_reviews WHERE puzzle_id = ?", (puzzle_id,))
    else:
        con.execute(
            "INSERT INTO puzzle_reviews (puzzle_id, box, due) VALUES (?, ?, date('now', 'localtime', ?)) "
            "ON CONFLICT (puzzle_id) DO UPDATE SET box = excluded.box, due = excluded.due",
            (puzzle_id, box, f"+{days} days"),
        )
    db.log_activity(con, "puzzle")
    con.commit()
    return {"rating": round(new_r), "delta": round(new_r - r), "rated": rated, "box": box, "due_in_days": days}


# ---------- own-blunder puzzles (run by the analysis worker, which owns the engine) ----------

def _unique_best(engine, board: chess.Board, limit) -> chess.Move | None:
    """The engine's best move if it is clearly the only good one (second best loses >= 15% win chance)."""
    infos = engine.analyse(board, limit, multipv=2)
    if not infos or "pv" not in infos[0]:
        return None
    best = infos[0]["pv"][0]
    if len(infos) == 1:
        return best
    pov = lambda info: review.win_percent(info["score"].pov(board.turn).score(), info["score"].pov(board.turn).mate())
    return best if pov(infos[0]) - pov(infos[1]) >= 15 else None


def make_own_puzzles(con: sqlite3.Connection, engine, run_id: int) -> int:
    run = con.execute(
        "SELECT r.id, r.depth, r.movetime_ms, g.id AS game_id, g.user_color, g.white_elo, g.black_elo "
        "FROM analysis_runs r JOIN games g ON g.id = r.game_id WHERE r.id = ?", (run_id,)
    ).fetchone()
    made = 0
    if run and run["user_color"]:
        rows = con.execute("SELECT * FROM move_evals WHERE run_id = ? ORDER BY ply", (run_id,)).fetchall()
        limit = chess.engine.Limit(time=run["movetime_ms"] / 1000) if run["movetime_ms"] else chess.engine.Limit(depth=min(run["depth"] or 14, 16))
        elo = run["white_elo"] if run["user_color"] == "white" else run["black_elo"]
        for row in rows:
            if row["ply"] < 2 or row["color"] != run["user_color"] or row["classification"] not in ("mistake", "blunder"):
                continue
            setup, before = rows[row["ply"] - 2], rows[row["ply"] - 1]
            board = chess.Board(before["fen"])
            solution = []
            for _ in range(3):  # up to 3 solver moves, each must be the only good move
                best = _unique_best(engine, board, limit)
                if best is None:
                    break
                solution.append(best.uci())
                board.push(best)
                if board.is_game_over():
                    break
                reply = engine.play(board, limit).move
                solution.append(reply.uci())
                board.push(reply)
            while solution and len(solution) % 2 == 0:  # must end on a solver move
                solution.pop()
            if not solution:
                continue
            themes = {PUZZLE_THEME[t["tag"]] for t in con.execute(
                "SELECT tag FROM move_tags WHERE run_id = ? AND ply = ? AND kind = 'missed'", (run_id, row["ply"]))}
            themes.add(row["phase"] or "middlegame")
            final = chess.Board(before["fen"])
            for u in solution:
                final.push_uci(u)
            if final.is_checkmate():
                themes.add("mate")
            moves = [before["move_uci"], *solution]
            cur = con.execute(
                "INSERT OR IGNORE INTO puzzles (source, source_id, fen, moves, rating, themes, game_id, ply) "
                "VALUES ('own', ?, ?, ?, ?, ?, ?, ?)",
                (f"{run['game_id']}:{row['ply']}", setup["fen"], " ".join(moves), elo or 1200, " ".join(sorted(themes)),
                 run["game_id"], row["ply"]),
            )
            if cur.rowcount:
                made += 1
                con.executemany("INSERT OR IGNORE INTO puzzle_themes (puzzle_id, theme) VALUES (?, ?)",
                                [(cur.lastrowid, t) for t in themes])
    con.execute("UPDATE analysis_runs SET puzzled = 1 WHERE id = ?", (run_id,))
    con.commit()
    return made


# ---------- Lichess puzzle DB ----------

LICHESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS lp (
    id TEXT PRIMARY KEY, fen TEXT NOT NULL, moves TEXT NOT NULL, rating INTEGER NOT NULL,
    rd INTEGER NOT NULL, popularity INTEGER, plays INTEGER, themes TEXT NOT NULL, opening TEXT
);
CREATE INDEX IF NOT EXISTS lp_rating ON lp(rating);
CREATE TABLE IF NOT EXISTS lp_themes (
    theme TEXT NOT NULL, rating INTEGER NOT NULL, id TEXT NOT NULL, PRIMARY KEY (theme, rating, id)
) WITHOUT ROWID;
"""


def lichess_db(path: Path) -> sqlite3.Connection:
    con = db.connect(path)
    con.executescript(LICHESS_SCHEMA)
    return con


def lichess_count(path: Path) -> int:
    if not path.exists():
        return 0
    con = lichess_db(path)
    try:
        return con.execute("SELECT count(*) FROM lp").fetchone()[0]
    finally:
        con.close()


class LichessImporter:
    """Streams lichess_db_puzzle.csv(.zst) into data/lichess_puzzles.db in a background thread."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.state = {"running": False, "imported": 0, "progress": 0.0, "error": None}
        self._thread: threading.Thread | None = None

    def start(self, source: Path, min_popularity: int = 80, min_plays: int = 300) -> None:
        if self.state["running"]:
            raise RuntimeError("An import is already running.")
        if not source.is_file():
            raise FileNotFoundError(f"File not found: {source}")
        self.state = {"running": True, "imported": 0, "progress": 0.0, "error": None}
        self._thread = threading.Thread(target=self._run, args=(source, min_popularity, min_plays), daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, source: Path, min_popularity: int, min_plays: int) -> None:
        try:
            import_lichess_file(source, self.db_path, min_popularity, min_plays, self.state)
        except Exception as exc:
            self.state["error"] = str(exc) or type(exc).__name__
        finally:
            self.state["running"] = False


def import_lichess_file(source: Path, db_path: Path, min_popularity: int = 80, min_plays: int = 300,
                        state: dict | None = None) -> int:
    state = state if state is not None else {}
    size = source.stat().st_size or 1
    raw = open(source, "rb")
    try:
        if source.suffix == ".zst":
            import zstandard

            stream = zstandard.ZstdDecompressor().stream_reader(raw)
        else:
            stream = raw
        reader = csv.reader(io.TextIOWrapper(stream, encoding="utf-8", newline=""))
        con = lichess_db(db_path)
        wanted = set(THEMES)
        batch, theme_rows, n = [], [], 0

        def flush():
            con.executemany("INSERT OR REPLACE INTO lp VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
            con.executemany("INSERT OR IGNORE INTO lp_themes VALUES (?, ?, ?)", theme_rows)
            con.commit()
            batch.clear()
            theme_rows.clear()
            state["imported"] = n
            state["progress"] = min(1.0, raw.tell() / size)

        for row in reader:
            if not row or row[0] == "PuzzleId" or len(row) < 8:
                continue
            pid, fen, moves, rating, rd, pop, plays, themes = row[:8]
            if int(pop) < min_popularity or int(plays) < min_plays:
                continue
            batch.append((pid, fen, moves, int(rating), int(rd), int(pop), int(plays), themes, row[9] if len(row) > 9 else ""))
            theme_rows += [(t, int(rating), pid) for t in themes.split() if t in wanted]
            n += 1
            if len(batch) >= 5000:
                flush()
        flush()
        state["progress"] = 1.0
        con.close()
        return n
    finally:
        raw.close()


def find_lichess_files(data_dir: Path) -> list[str]:
    places = [data_dir, Path.home() / "Downloads", data_dir.parent]
    found = []
    for place in places:
        for name in ("lichess_db_puzzle.csv.zst", "lichess_db_puzzle.csv"):
            p = place / name
            if p.is_file():
                found.append(str(p))
    return found


# ---------- selection ----------

def _store_lichess(con: sqlite3.Connection, row: sqlite3.Row) -> int:
    themes = [t for t in row["themes"].split() if t in THEMES]
    con.execute(
        "INSERT OR IGNORE INTO puzzles (source, source_id, fen, moves, rating, themes) VALUES ('lichess', ?, ?, ?, ?, ?)",
        (row["id"], row["fen"], row["moves"], row["rating"], " ".join(themes)),
    )
    pid = con.execute("SELECT id FROM puzzles WHERE source = 'lichess' AND source_id = ?", (row["id"],)).fetchone()[0]
    con.executemany("INSERT OR IGNORE INTO puzzle_themes (puzzle_id, theme) VALUES (?, ?)", [(pid, t) for t in themes])
    con.commit()
    return pid


def _pick_lichess(con: sqlite3.Connection, lichess_path: Path, theme: str | None, rating: float) -> int | None:
    if not lichess_path.exists():
        return None
    seen = {r[0] for r in con.execute(
        "SELECT p.source_id FROM puzzles p JOIN puzzle_attempts a ON a.puzzle_id = p.id WHERE p.source = 'lichess'")}
    lcon = lichess_db(lichess_path)
    try:
        for spread in (150, 400, 3000):
            target = int(rating + random.randint(-spread // 2, spread))
            for op, order in ((">=", "ASC"), ("<=", "DESC")):
                if theme:
                    rows = lcon.execute(
                        f"SELECT p.* FROM lp_themes t JOIN lp p ON p.id = t.id WHERE t.theme = ? AND t.rating {op} ? "
                        f"ORDER BY t.rating {order} LIMIT 60", (theme, target)).fetchall()
                else:
                    rows = lcon.execute(f"SELECT * FROM lp WHERE rating {op} ? ORDER BY rating {order} LIMIT 60", (target,)).fetchall()
                fresh = [r for r in rows if r["id"] not in seen]
                if fresh:
                    return _store_lichess(con, random.choice(fresh))
        return None
    finally:
        lcon.close()


def _pick_own(con: sqlite3.Connection, theme: str | None, exclude: list[int]) -> int | None:
    join = "JOIN puzzle_themes t ON t.puzzle_id = p.id AND t.theme = ?" if theme else ""
    skip = f"AND p.id NOT IN ({', '.join('?' * len(exclude))})" if exclude else ""
    row = con.execute(
        f"SELECT p.id FROM puzzles p {join} WHERE p.source = 'own' {skip} "
        "AND NOT EXISTS (SELECT 1 FROM puzzle_attempts a WHERE a.puzzle_id = p.id) ORDER BY p.id DESC LIMIT 1",
        ([theme] if theme else []) + exclude,
    ).fetchone()
    return row[0] if row else None


def _pick_review(con: sqlite3.Connection, theme: str | None, exclude: list[int]) -> int | None:
    join = "JOIN puzzle_themes t ON t.puzzle_id = r.puzzle_id AND t.theme = ?" if theme else ""
    params = ([theme] if theme else []) + exclude
    skip = f"AND r.puzzle_id NOT IN ({', '.join('?' * len(exclude))})" if exclude else ""
    row = con.execute(
        f"SELECT r.puzzle_id FROM puzzle_reviews r {join} WHERE r.due <= date('now', 'localtime') {skip} "
        "ORDER BY r.due, r.box LIMIT 1",
        params,
    ).fetchone()
    return row[0] if row else None


def next_puzzle(con: sqlite3.Connection, lichess_path: Path, mode: str, theme: str | None = None,
                exclude: list[int] | None = None, themes_pref: list[str] | None = None) -> dict | None:
    """mode: 'mixed' (due reviews, then Lichess, then own), 'own', 'review', 'lichess',
    or 'plan' (reviews, own blunders, then Lichess on your weakest themes)."""
    exclude = exclude or []
    rating, _ = player_rating(con)
    pid = None
    if mode in ("mixed", "review", "plan"):
        pid = _pick_review(con, theme, exclude)
    if pid is None and mode in ("own", "plan"):
        pid = _pick_own(con, theme, exclude)
    if pid is None and mode in ("mixed", "lichess", "plan"):
        pick_theme = theme or (random.choice(themes_pref) if mode == "plan" and themes_pref else None)
        pid = _pick_lichess(con, lichess_path, pick_theme, rating)
        if pid is None and pick_theme and not theme:
            pid = _pick_lichess(con, lichess_path, None, rating)
    if pid is None and mode in ("mixed", "plan"):
        pid = _pick_own(con, theme, exclude)
    return puzzle_dict(con, pid) if pid else None


def puzzle_dict(con: sqlite3.Connection, pid: int) -> dict:
    p = con.execute("SELECT * FROM puzzles WHERE id = ?", (pid,)).fetchone()
    rv = con.execute("SELECT box, due FROM puzzle_reviews WHERE puzzle_id = ?", (pid,)).fetchone()
    return {
        "id": p["id"], "source": p["source"], "fen": p["fen"], "moves": p["moves"].split(), "rating": p["rating"],
        "themes": [t for t in p["themes"].split() if t], "game_id": p["game_id"], "ply": p["ply"],
        "review_box": rv["box"] if rv else None,
        "attempts": con.execute("SELECT count(*) FROM puzzle_attempts WHERE puzzle_id = ?", (pid,)).fetchone()[0],
    }


def summary(con: sqlite3.Connection, lichess_path: Path) -> dict:
    r, rd = player_rating(con)
    one = lambda sql: con.execute(sql).fetchone()[0]
    themes = [{"theme": t, "label": THEME_LABELS[t]} for t in THEMES]
    own_themes = {r[0]: r[1] for r in con.execute(
        "SELECT t.theme, count(*) FROM puzzle_themes t JOIN puzzles p ON p.id = t.puzzle_id WHERE p.source = 'own' GROUP BY 1")}
    return {
        "rating": round(r), "rd": round(rd),
        "attempts": one("SELECT count(*) FROM puzzle_attempts"),
        "solved": one("SELECT count(*) FROM puzzle_attempts WHERE solved = 1"),
        "today": one("SELECT count(*) FROM puzzle_attempts WHERE date(created_at, 'localtime') = date('now', 'localtime')"),
        "due": one("SELECT count(*) FROM puzzle_reviews WHERE due <= date('now', 'localtime')"),
        "in_review": one("SELECT count(*) FROM puzzle_reviews"),
        "own_total": one("SELECT count(*) FROM puzzles WHERE source = 'own'"),
        "own_unseen": one("SELECT count(*) FROM puzzles p WHERE source = 'own' AND NOT EXISTS "
                          "(SELECT 1 FROM puzzle_attempts a WHERE a.puzzle_id = p.id)"),
        "lichess_count": lichess_count(lichess_path),
        "themes": [dict(t, own=own_themes.get(t["theme"], 0)) for t in themes],
    }
