"""PGN parsing, metadata extraction and deduplicated import."""
import hashlib
import io
import re
import sqlite3
from dataclasses import dataclass, field

import chess
import chess.pgn

from . import openings

FAMILY_WORDS = ("Defense", "Defence", "Game", "Opening", "Gambit", "Attack", "System")


@dataclass
class ImportResult:
    added: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)
    ids: list[int] = field(default_factory=list)


def iter_games(text: str):
    stream = io.StringIO(text.lstrip("﻿"))
    while (game := chess.pgn.read_game(stream)) is not None:
        yield game


def export_pgn(game: chess.pgn.Game, headers: bool = True) -> str:
    """Canonical PGN text (keeps comments such as [%clk], unwrapped lines)."""
    exporter = chess.pgn.StringExporter(headers=headers, variations=True, comments=True, columns=None)
    return game.accept(exporter).strip()


def dedupe_key(game: chess.pgn.Game) -> str:
    """Link header if present (chess.com), else a game URL in Site (lichess), else SHA1 of movetext."""
    link = game.headers.get("Link", "").strip()
    if link:
        return "link:" + link
    site = game.headers.get("Site", "").strip()
    if site.startswith(("http://", "https://")):
        return "link:" + site
    text = game.headers.get("FEN", "") + "\n" + export_pgn(game, headers=False)
    return "sha1:" + hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def time_class(tc: str | None) -> str | None:
    if not tc or tc in ("-", "?"):
        return None
    if "/" in tc:
        return "daily"
    m = re.fullmatch(r"(\d+)(?:\+(\d+))?", tc.strip())
    if not m:
        return None
    estimate = int(m[1]) + 40 * int(m[2] or 0)
    if estimate < 180:
        return "bullet"
    if estimate < 480:
        return "blitz"
    if estimate < 1500:
        return "rapid"
    return "classical"


def normalize_date(value: str | None) -> str | None:
    """'2024.03.15' -> '2024-03-15'; unknown parts are dropped ('2024.??.??' -> '2024')."""
    if not value:
        return None
    parts = []
    for part in value.replace("/", ".").split("."):
        if not part.isdigit():
            break
        parts.append(part)
    return "-".join(parts) or None


def opening_name(headers: chess.pgn.Headers) -> str | None:
    if headers.get("Opening", "?") not in ("?", ""):
        return headers["Opening"]
    url = headers.get("ECOUrl", "")
    if "/openings/" in url:
        words = []
        for word in url.rsplit("/openings/", 1)[1].split("-"):
            if re.match(r"\d+\.", word) or word == "with":
                break
            words.append(word)
        return " ".join(words) or None
    return None


def opening_family(name: str | None) -> str | None:
    if not name:
        return None
    head = re.split(r"[:,]", name, maxsplit=1)[0].strip()
    words = head.split()
    for i, word in enumerate(words):
        if word in FAMILY_WORDS:
            return " ".join(words[: i + 1])
    return head


def user_fields(white: str, black: str, result: str, usernames: list[str]) -> tuple[str | None, str | None]:
    names = {n.lower() for n in usernames}
    if white.lower() in names:
        color = "white"
    elif black.lower() in names:
        color = "black"
    else:
        return None, None
    if result == "1/2-1/2":
        return color, "draw"
    if result in ("1-0", "0-1"):
        won = (result == "1-0") == (color == "white")
        return color, "win" if won else "loss"
    return color, None


def _int(value: str | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def game_row(game: chess.pgn.Game, usernames: list[str]) -> dict:
    h = game.headers
    white, black, result = h.get("White", "?"), h.get("Black", "?"), h.get("Result", "*")
    color, outcome = user_fields(white, black, result, usernames)
    name = opening_name(h)
    tc = h.get("TimeControl")
    link = h.get("Link") or (h.get("Site") if h.get("Site", "").startswith("http") else None)
    return {
        "dedupe_key": dedupe_key(game),
        "pgn": export_pgn(game),
        "link": link,
        "event": h.get("Event"),
        "site": h.get("Site"),
        "date": normalize_date(h.get("Date")) or normalize_date(h.get("UTCDate")),
        "white": white,
        "black": black,
        "white_elo": _int(h.get("WhiteElo")),
        "black_elo": _int(h.get("BlackElo")),
        "result": result,
        "user_color": color,
        "user_result": outcome,
        "eco": h.get("ECO") if h.get("ECO", "?") != "?" else None,
        "opening": name,
        "opening_family": opening_family(name),
        "time_control": tc,
        "time_class": time_class(tc),
        "termination": h.get("Termination"),
        "ply_count": sum(1 for _ in game.mainline_moves()),
    }


def _label(n: int, game: chess.pgn.Game) -> str:
    return f"Game {n} ({game.headers.get('White') or '?'} vs {game.headers.get('Black') or '?'})"


def import_pgn(con: sqlite3.Connection, text: str, usernames: list[str]) -> ImportResult:
    res = ImportResult()
    for n, game in enumerate(iter_games(text), 1):
        variant = game.headers.get("Variant", "Standard")
        if variant.lower() not in ("standard", "from position", ""):
            res.errors.append(f"{_label(n, game)}: variant '{variant}' not supported")
            continue
        if game.errors:
            res.errors.append(f"{_label(n, game)}: {game.errors[0]}")
            continue
        if game.next() is None:
            res.errors.append(f"{_label(n, game)}: no moves")
            continue
        row = game_row(game, usernames)
        found = openings.classify(con, game)  # book name beats site-specific headers
        if found:
            row.update(eco=found[0], opening=found[1], opening_family=openings.family(found[1]))
        cols = ", ".join(row)
        cur = con.execute(
            f"INSERT OR IGNORE INTO games ({cols}) VALUES ({', '.join('?' * len(row))})",
            tuple(row.values()),
        )
        if cur.rowcount:
            res.added += 1
            res.ids.append(cur.lastrowid)
        else:
            res.duplicates += 1
    con.commit()
    return res


def refresh_user_fields(con: sqlite3.Connection, usernames: list[str]) -> None:
    """Recompute user color/result after the configured username changes."""
    rows = con.execute("SELECT id, white, black, result, user_color, user_result FROM games").fetchall()
    for r in rows:
        color, outcome = user_fields(r["white"] or "", r["black"] or "", r["result"] or "", usernames)
        if (color, outcome) != (r["user_color"], r["user_result"]):
            con.execute("UPDATE games SET user_color=?, user_result=? WHERE id=?", (color, outcome, r["id"]))
    con.commit()


def game_moves(pgn: str) -> dict:
    """Mainline moves with FEN after each move and clock (seconds) for the board viewer."""
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    start = board.fen()
    moves = []
    for node in game.mainline():
        san = board.san(node.move)
        board.push(node.move)
        moves.append({
            "san": san,
            "uci": node.move.uci(),
            "fen": board.fen(),
            "check": board.is_check(),
            "clock": node.clock(),
        })
    return {"start_fen": start, "moves": moves}
