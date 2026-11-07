"""Play against Stockfish (full games and endgame positions). One interactive engine, reused and locked."""
import json
import sqlite3
import threading
from datetime import date, datetime
from functools import lru_cache

import chess
import chess.engine
import chess.pgn

from . import db, ingest
from .config import ROOT

ENDGAMES_FILE = ROOT / "content" / "endgames.json"
MIN_UCI_ELO = 1320  # Stockfish's lowest UCI_Elo; weaker levels use Skill Level + shallow search


def strength_options(elo: int) -> tuple[dict, chess.engine.Limit]:
    """Engine options and search limit for a target playing strength (~600-3000)."""
    if elo >= 3000:
        return {"UCI_LimitStrength": False, "Skill Level": 20}, chess.engine.Limit(time=0.5)
    if elo >= MIN_UCI_ELO:
        return {"UCI_LimitStrength": True, "UCI_Elo": elo, "Skill Level": 20}, chess.engine.Limit(time=0.3)
    skill = max(0, min(5, round((elo - 600) / 144)))  # 600 -> 0 ... 1320 -> 5
    depth = max(1, min(5, 1 + (elo - 600) // 180))
    return {"UCI_LimitStrength": False, "Skill Level": skill}, chess.engine.Limit(depth=depth, time=0.2)


class PlayEngine:
    def __init__(self, path: str | None):
        self.path = path
        self._engine: chess.engine.SimpleEngine | None = None
        self._options: dict = {}
        self._lock = threading.Lock()

    def move(self, fen: str, elo: int) -> chess.Move:
        if not self.path:
            raise RuntimeError("Stockfish not found. Set [engine] path in config.toml.")
        board = chess.Board(fen)
        if not board.is_valid():  # Stockfish crashes on impossible positions
            raise ValueError("Illegal position")
        if board.is_game_over():
            raise ValueError("Game is over")
        options, limit = strength_options(elo)
        with self._lock:
            if self._engine is None:
                self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
                self._engine.configure({"Threads": 1, "Hash": 64})
                self._options = {}
            changed = {k: v for k, v in options.items() if self._options.get(k) != v}
            if changed:
                self._engine.configure(changed)
                self._options.update(changed)
            try:
                return self._engine.play(board, limit).move
            except chess.engine.EngineTerminatedError:
                self._engine = None
                raise

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except Exception:
                    pass
                self._engine = None


@lru_cache(maxsize=1)
def endgames() -> tuple[dict, ...]:
    return tuple(json.loads(ENDGAMES_FILE.read_text(encoding="utf-8")))


def endgame_list(con: sqlite3.Connection) -> list[dict]:
    prog = {r["id"]: dict(r) for r in con.execute("SELECT * FROM endgame_progress")}
    return [dict(e, attempts=prog.get(e["id"], {}).get("attempts", 0), successes=prog.get(e["id"], {}).get("successes", 0))
            for e in endgames()]


def record_endgame(con: sqlite3.Connection, endgame_id: str, success: bool) -> dict:
    if not any(e["id"] == endgame_id for e in endgames()):
        raise KeyError(endgame_id)
    con.execute(
        "INSERT INTO endgame_progress (id, attempts, successes) VALUES (?, 1, ?) ON CONFLICT (id) DO UPDATE SET "
        "attempts = attempts + 1, successes = successes + excluded.successes, updated_at = datetime('now')",
        (endgame_id, int(success)),
    )
    db.log_activity(con, "play")
    con.commit()
    return dict(con.execute("SELECT * FROM endgame_progress WHERE id = ?", (endgame_id,)).fetchone())


def save_game(con: sqlite3.Connection, usernames: list[str], start_fen: str, moves: list[str], user_color: str,
              result: str, elo: int, termination: str) -> int | None:
    """Store a finished game in the library (tagged 'vs-engine'); returns its id."""
    board = chess.Board(start_fen)
    if not board.is_valid():
        raise ValueError("Illegal start position")
    game = chess.pgn.Game()
    if start_fen != chess.STARTING_FEN:
        game.setup(board)
    node = game
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal move {uci}")
        node = node.add_variation(move)
        board.push(move)
    me = usernames[0] if usernames else "You"
    bot = f"Stockfish ({elo})"
    h = game.headers
    h["Event"] = "Chezz vs Stockfish"
    h["Site"] = "Chezz"
    h["Date"] = date.today().strftime("%Y.%m.%d")
    h["White"], h["Black"] = (me, bot) if user_color == "white" else (bot, me)
    h["Result"] = result
    h["Termination"] = termination
    h[("Black" if user_color == "white" else "White") + "Elo"] = str(elo)
    # Unique per game even if the moves repeat an earlier game.
    h["Link"] = f"chezz://play/{datetime.now():%Y%m%d-%H%M%S-%f}"
    res = ingest.import_pgn(con, str(game), usernames)
    if not res.ids:
        return None
    gid = res.ids[0]
    con.execute("INSERT OR IGNORE INTO game_tags (game_id, tag) VALUES (?, 'vs-engine')", (gid,))
    db.log_activity(con, "play")
    con.commit()
    return gid

