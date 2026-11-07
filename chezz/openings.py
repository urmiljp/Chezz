"""Opening book (Lichess ECO table), repertoire built from your games, drills and book deviations."""
import io
import sqlite3
from collections import Counter, defaultdict
from functools import lru_cache

import chess
import chess.pgn

from . import db
from .config import ROOT

ECO_DIR = ROOT / "content" / "eco"
MAX_PLY = 16          # repertoire depth (8 moves each)
DRILL_DAYS = [1, 3, 7, 14, 30]


@lru_cache(maxsize=1)
def _eco_data() -> tuple[list[tuple], list[tuple], str]:
    """Parse the ECO TSVs once per process: (eco rows, book rows, version)."""
    eco, book = {}, {}
    files = sorted(ECO_DIR.glob("*.tsv"))
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            code, name, pgn = parts[:3]
            board = chess.Board()
            for tok in pgn.split():
                if tok.endswith("."):
                    continue
                move = board.parse_san(tok)
                book.setdefault((board.epd(), move.uci()), tok)
                board.push(move)
            eco.setdefault(board.epd(), (code, name, board.ply()))
    version = "|".join(f"{f.name}:{f.stat().st_size}" for f in files)
    return ([(k, *v) for k, v in eco.items()], [(k[0], k[1], v) for k, v in book.items()], version)


def load_eco(con: sqlite3.Connection) -> bool:
    """Fill the eco/book tables if missing or outdated. True if (re)loaded."""
    eco, book, version = _eco_data()
    if db.kv_get(con, "eco_version") == version:
        return False
    con.execute("DELETE FROM eco")
    con.execute("DELETE FROM book")
    con.executemany("INSERT INTO eco (epd, eco, name, ply) VALUES (?, ?, ?, ?)", eco)
    con.executemany("INSERT INTO book (epd, move_uci, san) VALUES (?, ?, ?)", book)
    db.kv_set(con, "eco_version", version)
    con.commit()
    return True


def family(name: str | None) -> str | None:
    return name.split(":")[0].strip() if name else None


def classify(con: sqlite3.Connection, game: chess.pgn.Game) -> tuple[str, str] | None:
    """(ECO code, name) of the deepest named book position the game reached."""
    board = game.board()
    best = None
    for i, move in enumerate(game.mainline_moves()):
        if i >= 40:
            break
        board.push(move)
        row = con.execute("SELECT eco, name FROM eco WHERE epd = ?", (board.epd(),)).fetchone()
        if row:
            best = (row["eco"], row["name"])
    return best


def reclassify_all(con: sqlite3.Connection) -> None:
    for g in con.execute("SELECT id, pgn FROM games").fetchall():
        found = classify(con, chess.pgn.read_game(io.StringIO(g["pgn"])))
        if found:
            con.execute("UPDATE games SET eco = ?, opening = ?, opening_family = ? WHERE id = ?",
                        (found[0], found[1], family(found[1]), g["id"]))
    con.commit()


def _book_positions(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT epd FROM book UNION SELECT epd FROM eco")}


def _user_games(con: sqlite3.Connection, color: str | None = None, limit: int = 2000):
    sql = "SELECT id, pgn, user_color, user_result, date, white, black FROM games WHERE user_color IS NOT NULL"
    params: tuple = ()
    if color:
        sql += " AND user_color = ?"
        params = (color,)
    return con.execute(sql + " ORDER BY date DESC, id DESC LIMIT ?", (*params, limit)).fetchall()


def _walk(pgn: str, max_ply: int = MAX_PLY):
    """Yield (board before move, move) for the first plies of a game."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    for i, move in enumerate(game.mainline_moves()):
        if i >= max_ply:
            break
        yield board, move
        board.push(move)


def build_repertoire(con: sqlite3.Connection, color: str) -> int:
    """Add a card for every position (you to move) on your usual lines; keeps manual choices and review state."""
    me = chess.WHITE if color == "white" else chess.BLACK
    mine: dict[str, Counter] = defaultdict(Counter)
    theirs: dict[str, Counter] = defaultdict(Counter)
    for g in _user_games(con, color):
        for board, move in _walk(g["pgn"]):
            (mine if board.turn == me else theirs)[board.epd()][move.uci()] += 1
    in_book = _book_positions(con)
    existing = {r["epd"]: r["move_uci"] for r in con.execute("SELECT epd, move_uci FROM repertoire WHERE color = ?", (color,))}

    added, seen = 0, set()
    stack: list[tuple[chess.Board, list[str]]] = [(chess.Board(), [])]
    while stack:
        board, line = stack.pop()
        key = board.epd()
        if key in seen or board.ply() >= MAX_PLY:
            continue
        seen.add(key)
        if board.turn == me:
            uci = existing.get(key) or (mine[key].most_common(1)[0][0] if mine[key] else None)
            if uci is None:
                continue
            move = chess.Move.from_uci(uci)
            if key not in existing:
                con.execute(
                    "INSERT OR IGNORE INTO repertoire (color, epd, fen, line, move_uci, san, due) "
                    "VALUES (?, ?, ?, ?, ?, ?, date('now', 'localtime'))",
                    (color, key, board.fen(), " ".join(line), uci, board.san(move)),
                )
                added += 1
            nxt = board.copy(stack=False)
            nxt.push(move)
            stack.append((nxt, line + [uci]))
        else:
            for uci, n in theirs[key].items():
                nxt = board.copy(stack=False)
                nxt.push_uci(uci)
                if n >= 2 or nxt.epd() in in_book:  # skip one-off sidelines outside book
                    stack.append((nxt, line + [uci]))
    con.commit()
    return added


def set_move(con: sqlite3.Connection, color: str, fen: str, line: list[str], uci: str) -> None:
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    if move not in board.legal_moves:
        raise ValueError("Illegal move")
    con.execute(
        "INSERT INTO repertoire (color, epd, fen, line, move_uci, san, manual, box, due) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, 0, date('now', 'localtime')) "
        "ON CONFLICT (color, epd) DO UPDATE SET move_uci = excluded.move_uci, san = excluded.san, manual = 1, "
        "box = 0, due = excluded.due",
        (color, board.epd(), fen, " ".join(line), uci, board.san(move)),
    )
    con.commit()


def explore(con: sqlite3.Connection, color: str, fen: str) -> dict:
    """Candidate moves in a position: your games (count, score), book moves, and your repertoire choice."""
    board = chess.Board(fen)
    key = board.epd()
    stats: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for g in _user_games(con, color, limit=1000):
        for b, move in _walk(g["pgn"], 30):
            if b.epd() == key:
                s = stats[move.uci()]
                s[0] += 1
                s[1] += {"win": 1.0, "draw": 0.5}.get(g["user_result"], 0.0)
                break
    book = {r["move_uci"]: r["san"] for r in con.execute("SELECT move_uci, san FROM book WHERE epd = ?", (key,))}
    rep = con.execute("SELECT move_uci, manual FROM repertoire WHERE color = ? AND epd = ?", (color, key)).fetchone()
    name = con.execute("SELECT eco, name FROM eco WHERE epd = ?", (key,)).fetchone()
    cands = []
    for uci in set(stats) | set(book):
        n, pts = stats.get(uci, (0, 0.0))
        cands.append({"uci": uci, "san": board.san(chess.Move.from_uci(uci)), "games": n,
                      "score": round(100 * pts / n) if n else None, "book": uci in book,
                      "repertoire": bool(rep and rep["move_uci"] == uci)})
    cands.sort(key=lambda c: (-c["repertoire"], -c["games"], not c["book"], c["san"]))
    return {"fen": board.fen(), "your_turn": (board.turn == chess.WHITE) == (color == "white"),
            "eco": name["eco"] if name else None, "name": name["name"] if name else None,
            "candidates": cands, "repertoire_move": rep["move_uci"] if rep else None,
            "manual": bool(rep and rep["manual"])}


def due_cards(con: sqlite3.Connection, color: str | None = None) -> int:
    sql = "SELECT count(*) FROM repertoire WHERE due <= date('now', 'localtime')"
    return con.execute(sql + (" AND color = ?" if color else ""), (color,) if color else ()).fetchone()[0]


def next_card(con: sqlite3.Connection, color: str) -> dict | None:
    row = con.execute(
        "SELECT * FROM repertoire WHERE color = ? AND due <= date('now', 'localtime') ORDER BY due, box, length(line) LIMIT 1",
        (color,),
    ).fetchone()
    if row is None:
        return None
    return {"epd": row["epd"], "fen": row["fen"], "line": row["line"].split(), "move_uci": row["move_uci"],
            "san": row["san"], "box": row["box"], "color": color, "due_left": due_cards(con, color)}


def grade_card(con: sqlite3.Connection, color: str, epd: str, correct: bool) -> dict:
    row = con.execute("SELECT box FROM repertoire WHERE color = ? AND epd = ?", (color, epd)).fetchone()
    if row is None:
        raise KeyError(epd)
    box = min(row["box"] + 1, len(DRILL_DAYS)) if correct else 1
    days = DRILL_DAYS[box - 1]
    con.execute("UPDATE repertoire SET box = ?, due = date('now', 'localtime', ?) WHERE color = ? AND epd = ?",
                (box, f"+{days} days", color, epd))
    db.log_activity(con, "drill")
    con.commit()
    return {"box": box, "due_in_days": days}


def deviations(con: sqlite3.Connection, pgn: str, user_color: str | None, in_book: set[str] | None = None) -> dict:
    """First move that left the opening book, and first move of yours that left your repertoire."""
    if '[FEN "' in pgn:  # started from a set position (endgame practice): no opening to compare
        return {"left_book": None, "left_repertoire": None}
    in_book = in_book if in_book is not None else _book_positions(con)
    rep = {}
    if user_color:
        rep = {r["epd"]: (r["move_uci"], r["san"]) for r in con.execute(
            "SELECT epd, move_uci, san FROM repertoire WHERE color = ?", (user_color,))}
    me = chess.WHITE if user_color == "white" else chess.BLACK
    left_book = left_rep = None
    for board, move in _walk(pgn, 40):
        ply = board.ply() + 1
        label = f"{board.fullmove_number}{'.' if board.turn == chess.WHITE else '...'} {board.san(move)}"
        who = None if not user_color else ("you" if board.turn == me else "opponent")
        if left_rep is None and user_color and board.turn == me and board.epd() in rep and rep[board.epd()][0] != move.uci():
            left_rep = {"ply": ply, "move": label, "expected": rep[board.epd()][1]}
        if left_book is None:
            after = board.copy(stack=False)
            after.push(move)
            if after.epd() not in in_book:
                book_moves = [r["san"] for r in con.execute("SELECT san FROM book WHERE epd = ?", (board.epd(),))]
                left_book = {"ply": ply, "move": label, "by": who, "book_moves": book_moves[:5]}
        if left_book and (left_rep or not user_color):
            break
    return {"left_book": left_book, "left_repertoire": left_rep}


def recent_deviations(con: sqlite3.Connection, limit: int = 30) -> list[dict]:
    in_book = _book_positions(con)
    out = []
    for g in _user_games(con, limit=limit):
        if '[FEN "' in g["pgn"]:
            continue
        d = deviations(con, g["pgn"], g["user_color"], in_book)
        out.append({"game_id": g["id"], "date": g["date"], "white": g["white"], "black": g["black"],
                    "user_color": g["user_color"], "user_result": g["user_result"], **d})
    return out


def summary(con: sqlite3.Connection) -> dict:
    out = {}
    for color in ("white", "black"):
        out[color] = {
            "cards": con.execute("SELECT count(*) FROM repertoire WHERE color = ?", (color,)).fetchone()[0],
            "due": due_cards(con, color),
            "games": con.execute("SELECT count(*) FROM games WHERE user_color = ?", (color,)).fetchone()[0],
        }
    return out
