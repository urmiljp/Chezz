"""Lessons from content/lessons/*.json, progress, and weakness-based recommendations.

Opening lessons give `side` and `lines` (annotated PGN from the start position) instead of `examples`;
each line becomes a playable example and the drill replays the lines from memory.
"""
import io
import json
import sqlite3
from functools import lru_cache

import chess
import chess.pgn

from . import db
from .config import ROOT

LESSON_DIR = ROOT / "content" / "lessons"


def _expand_line(line: dict, side: str) -> dict:
    """Annotated PGN line -> example: you play `side`, the other side's moves are played for you."""
    game = chess.pgn.read_game(io.StringIO(line["pgn"]))
    if game is None or game.errors:
        raise ValueError(f"Bad line {line['name']!r}: {game.errors if game else 'empty'}")
    nodes = list(game.mainline())
    moves = [n.move.uci() for n in nodes]
    if (len(moves) % 2 == 1) != (side == "white"):
        raise ValueError(f"Line {line['name']!r} must end on a {side} move")
    black = side == "black"
    return {
        "fen": chess.STARTING_FEN, "setup": moves[0] if black else None, "solution": moves[black:],
        "accept": [], "prompt": line["name"], "explain": line.get("explain", ""), "orientation": side,
        "san": [n.san() for n in nodes], "notes": [n.comment.strip() or None for n in nodes],
    }


def _prepare(lesson: dict) -> dict:
    if "lines" in lesson:
        lesson["examples"] = [_expand_line(line, lesson["side"]) for line in lesson.pop("lines")]
        # Lines share their first moves: a move without a note borrows the note an earlier line gave it.
        known: dict[tuple, str] = {}
        for ex in lesson["examples"]:
            moves = ([ex["setup"]] if ex["setup"] else []) + ex["solution"]
            for i, note in enumerate(ex["notes"]):
                key = tuple(moves[:i + 1])
                ex["notes"][i] = note or known.get(key)
                if ex["notes"][i]:
                    known.setdefault(key, ex["notes"][i])
        lesson["drill"] = {"themes": ["opening"], "count": len(lesson["examples"]), "mode": "lines"}
    return lesson


@lru_cache(maxsize=1)
def load_all() -> tuple[dict, ...]:
    lessons = [_prepare(json.loads(p.read_text(encoding="utf-8"))) for p in LESSON_DIR.glob("*.json")]
    return tuple(sorted(lessons, key=lambda l: l["order"]))


def get(lesson_id: str) -> dict | None:
    return next((l for l in load_all() if l["id"] == lesson_id), None)


def _progress(con: sqlite3.Connection) -> dict[str, dict]:
    return {r["lesson_id"]: dict(r) for r in con.execute("SELECT * FROM lesson_progress")}


def listing(con: sqlite3.Connection, weaknesses: list[dict]) -> list[dict]:
    """All lessons with progress; the ones matching your top weaknesses are marked recommended."""
    prog = _progress(con)
    rank = {w["key"]: i for i, w in enumerate(weaknesses)}
    out = []
    for l in load_all():
        p = prog.get(l["id"], {})
        hits = [w for w in weaknesses if w["key"] in l.get("weakness", [])]
        out.append({
            "id": l["id"], "title": l["title"], "category": l["category"], "summary": l["summary"],
            "themes": l["drill"]["themes"], "examples": len(l["examples"]),
            "completed": bool(p.get("completed_at")), "started": bool(p),
            "drill_correct": p.get("drill_correct"), "drill_total": p.get("drill_total"),
            "reason": f"{hits[0]['label']}: {hits[0]['detail']}" if hits else None,
            "_rank": min((rank[w["key"]] for w in hits), default=99),
        })
    # One lesson per weakness (the first unfinished one), for the top three weaknesses that have a lesson.
    todo, used = [], set()
    for l in sorted((l for l in out if not l["completed"] and l["_rank"] < 99), key=lambda l: l["_rank"]):
        if l["_rank"] not in used and len(todo) < 3:
            todo.append(l)
            used.add(l["_rank"])
    if not todo:
        todo = [l for l in out if not l["completed"]][:1]
    for l in out:
        l["recommended"] = l in todo
        del l["_rank"]
    return out


def recommended(con: sqlite3.Connection, weaknesses: list[dict]) -> dict | None:
    items = [l for l in listing(con, weaknesses) if l["recommended"]]
    items.sort(key=lambda l: (l["reason"] is None,))
    return items[0] if items else None


def save_progress(con: sqlite3.Connection, lesson_id: str, examples_done: int | None = None,
                  drill_correct: int | None = None, drill_total: int | None = None, completed: bool = False) -> dict:
    if get(lesson_id) is None:
        raise KeyError(lesson_id)
    con.execute("INSERT OR IGNORE INTO lesson_progress (lesson_id) VALUES (?)", (lesson_id,))
    if examples_done is not None:
        con.execute("UPDATE lesson_progress SET examples_done = max(examples_done, ?) WHERE lesson_id = ?", (examples_done, lesson_id))
    if drill_total is not None:
        con.execute("UPDATE lesson_progress SET drill_correct = ?, drill_total = ? WHERE lesson_id = ?",
                    (drill_correct or 0, drill_total, lesson_id))
    if completed:
        con.execute("UPDATE lesson_progress SET completed_at = coalesce(completed_at, datetime('now')) WHERE lesson_id = ?", (lesson_id,))
    con.execute("UPDATE lesson_progress SET updated_at = datetime('now') WHERE lesson_id = ?", (lesson_id,))
    db.log_activity(con, "lesson")
    con.commit()
    return dict(con.execute("SELECT * FROM lesson_progress WHERE lesson_id = ?", (lesson_id,)).fetchone())


def add_to_repertoire(con: sqlite3.Connection, lesson_id: str) -> dict:
    """Put an opening lesson's lines into your repertoire (drilled with spaced repetition).
    Positions where your repertoire already has a different move are left alone."""
    lesson = get(lesson_id)
    if lesson is None or "side" not in lesson:
        raise KeyError(lesson_id)
    color = lesson["side"]
    me = chess.WHITE if color == "white" else chess.BLACK
    added = kept = 0
    for ex in lesson["examples"]:
        board, line = chess.Board(), []
        for uci in ([ex["setup"]] if ex["setup"] else []) + ex["solution"]:
            if board.turn == me:
                move = chess.Move.from_uci(uci)
                cur = con.execute(
                    "INSERT INTO repertoire (color, epd, fen, line, move_uci, san, manual, box, due) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, 0, date('now', 'localtime')) ON CONFLICT (color, epd) DO NOTHING",
                    (color, board.epd(), board.fen(), " ".join(line), uci, board.san(move)))
                if cur.rowcount:
                    added += 1
                elif con.execute("SELECT move_uci FROM repertoire WHERE color = ? AND epd = ?",
                                 (color, board.epd())).fetchone()[0] != uci:
                    kept += 1
            board.push_uci(uci)
            line.append(uci)
    con.commit()
    return {"color": color, "added": added, "kept": kept}
