"""Background engine analysis (one reused Stockfish process) and reading stored reviews."""
import io
import os
import re
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from . import db, puzzles, review, tactics
from .config import ROOT, Config

ERROR_CLASSES = ("inaccuracy", "mistake", "blunder")


def find_engine(cfg: Config) -> str | None:
    """Configured path, else `stockfish` on PATH, else ./engine/, else /usr/games (Debian/Ubuntu),
    else a winget install (Windows)."""
    if cfg.engine_path:
        p = Path(cfg.engine_path).expanduser()
        return str(p) if p.is_file() else None
    if found := shutil.which("stockfish"):
        return found
    # ./engine/ may hold the binary itself or the unpacked official download (engine/stockfish/stockfish-...).
    candidates = sorted(p for p in (ROOT / "engine").rglob("stockfish*") if p.is_file() and p.suffix.lower() in ("", ".exe"))
    if sys.platform != "win32":
        candidates.append(Path("/usr/games/stockfish"))
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        packages = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
        candidates += sorted(packages.glob("Stockfish*/**/stockfish*.exe"))
    return next((str(p) for p in candidates if p.is_file()), None)


def mover(ply_index: int, start_white: bool) -> str:
    """Color making the move at 0-based move index."""
    return "white" if (ply_index % 2 == 0) == start_white else "black"


def evaluate(engine: chess.engine.SimpleEngine, board: chess.Board, limit: chess.engine.Limit, game_key) -> dict:
    if board.is_checkmate():
        return {"cp": None, "mate": None, "win": 0.0 if board.turn == chess.WHITE else 100.0, "best": None, "pv": []}
    if board.is_game_over():
        return {"cp": None, "mate": None, "win": 50.0, "best": None, "pv": []}
    info = engine.analyse(board, limit, game=game_key)
    score = info["score"].white()
    cp, mate = score.score(), score.mate()
    pv = [m.uci() for m in info.get("pv", [])[:10]]
    return {"cp": cp, "mate": mate, "win": review.win_percent(cp, mate), "best": pv[0] if pv else None, "pv": pv}


def build_rows(fens: list[str], moves: list[tuple[str, str]], evals: list[dict], clocks: list, tc: str | None,
               start_white: bool = True) -> list[dict]:
    """Combine per-position evals into move_evals rows (ply 0 = start position)."""
    clock_info = review.clock_data(clocks, tc, start_white)
    rows = []
    for ply, (fen, ev) in enumerate(zip(fens, evals)):
        row = {"ply": ply, "fen": fen, "cp": ev["cp"], "mate": ev["mate"], "win": ev["win"],
               "best_uci": ev["best"], "pv": " ".join(ev["pv"]), "move_uci": None, "san": None, "color": None,
               "classification": None, "win_drop": None, "accuracy": None, "clock": None, "time_spent": None,
               "time_trouble": 0}
        if ply > 0:
            uci, san = moves[ply - 1]
            color = mover(ply - 1, start_white)
            before = evals[ply - 1]
            cls, drop = review.classify(before["win"], ev["win"], color, uci, before["best"])
            c = clock_info[ply - 1]
            row.update(move_uci=uci, san=san, color=color, classification=cls, win_drop=round(drop, 2),
                       accuracy=round(review.move_accuracy(before["win"], ev["win"], color), 1),
                       clock=c["clock"], time_spent=c["time_spent"], time_trouble=int(c["time_trouble"]))
        rows.append(row)
    return rows


class AnalysisWorker:
    """Processes queued analysis_runs one at a time in a daemon thread; the engine is started once and reused."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.engine_path = find_engine(cfg)
        self.engine_name: str | None = None
        self._engine: chess.engine.SimpleEngine | None = None
        self._wake = threading.Event()
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        con = db.connect(self.cfg.db_path)
        con.execute("UPDATE analysis_runs SET status = 'queued', progress = 0 WHERE status = 'running'")
        con.commit()
        con.close()
        self._thread = threading.Thread(target=self._loop, name="analysis", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._close_engine()

    def notify(self) -> None:
        self._wake.set()

    def _get_engine(self) -> chess.engine.SimpleEngine:
        if self._engine is None:
            if not self.engine_path:
                raise RuntimeError("Stockfish not found. Set [engine] path in config.toml.")
            self._engine = chess.engine.SimpleEngine.popen_uci(self.engine_path)
            self._engine.configure({"Threads": self.cfg.threads, "Hash": self.cfg.hash_mb})
            self.engine_name = self._engine.id.get("name", Path(self.engine_path).stem)
        return self._engine

    def _close_engine(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
            self._engine = None

    def _loop(self) -> None:
        con = db.connect(self.cfg.db_path)
        while not self._stop:
            self._wake.clear()
            run = con.execute(
                "SELECT r.id, r.depth, r.movetime_ms, g.pgn, g.time_control FROM analysis_runs r "
                "JOIN games g ON g.id = r.game_id WHERE r.status = 'queued' ORDER BY r.id LIMIT 1"
            ).fetchone()
            if run is None:
                if not self._postprocess(con):
                    self._wake.wait()
                continue
            try:
                self._analyse(con, run)
            except Exception as exc:
                con.rollback()
                con.execute(
                    "UPDATE analysis_runs SET status = 'error', error = ?, finished_at = datetime('now') WHERE id = ?",
                    (str(exc) or type(exc).__name__, run["id"]),
                )
                con.commit()
                if isinstance(exc, (chess.engine.EngineError, chess.engine.EngineTerminatedError)):
                    self._close_engine()
        con.close()

    def _postprocess(self, con: sqlite3.Connection) -> bool:
        """Idle-time work on finished runs: tactic tags, then puzzles from your blunders. True if it did work."""
        row = con.execute("SELECT id FROM analysis_runs WHERE status = 'done' AND tagged = 0 ORDER BY id LIMIT 1").fetchone()
        if row:
            tactics.tag_run(con, row["id"])
            return True
        if not self.engine_path:
            return False
        row = con.execute(
            "SELECT id FROM analysis_runs WHERE status = 'done' AND puzzled = 0 "
            "AND id IN (SELECT run_id FROM latest_runs) ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return False
        try:
            puzzles.make_own_puzzles(con, self._get_engine(), row["id"])
        except Exception:
            con.rollback()
            con.execute("UPDATE analysis_runs SET puzzled = 1 WHERE id = ?", (row["id"],))
            con.commit()
            self._close_engine()
        return True

    def _analyse(self, con: sqlite3.Connection, run: sqlite3.Row) -> None:
        game = chess.pgn.read_game(io.StringIO(run["pgn"]))
        board = game.board()
        start_white = board.turn == chess.WHITE
        fens, moves, clocks = [board.fen()], [], []
        boards = [board.copy(stack=False)]
        for node in game.mainline():
            moves.append((node.move.uci(), board.san(node.move)))
            board.push(node.move)
            fens.append(board.fen())
            boards.append(board.copy(stack=False))
            clocks.append(node.clock())

        engine = self._get_engine()
        limit = chess.engine.Limit(time=run["movetime_ms"] / 1000) if run["movetime_ms"] else chess.engine.Limit(depth=run["depth"])
        con.execute(
            "UPDATE analysis_runs SET status = 'running', engine = ?, total = ?, progress = 0 WHERE id = ?",
            (self.engine_name, len(boards), run["id"]),
        )
        con.commit()

        evals = []
        for i, b in enumerate(boards):
            if self._stop:
                return  # left as 'running'; reset to 'queued' on next start
            evals.append(evaluate(engine, b, limit, game_key=run["id"]))
            con.execute("UPDATE analysis_runs SET progress = ? WHERE id = ?", (i + 1, run["id"]))
            con.commit()

        rows = build_rows(fens, moves, evals, clocks, run["time_control"], start_white)
        acc = review.game_accuracy([e["win"] for e in evals], start_white)
        cols = list(rows[0])
        con.executemany(
            f"INSERT INTO move_evals (run_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            [(run["id"], *r.values()) for r in rows],
        )
        con.execute(
            "UPDATE analysis_runs SET status = 'done', white_accuracy = ?, black_accuracy = ?, "
            "finished_at = datetime('now') WHERE id = ?",
            (acc["white"], acc["black"], run["id"]),
        )
        con.commit()
        tactics.tag_run(con, run["id"])


def queue_game(con: sqlite3.Connection, cfg: Config, game_id: int) -> None:
    """Queue an analysis run for a game unless one is already queued or running."""
    con.execute(
        "INSERT INTO analysis_runs (game_id, depth, movetime_ms) SELECT ?, ?, ? WHERE NOT EXISTS "
        "(SELECT 1 FROM analysis_runs WHERE game_id = ? AND status IN ('queued', 'running'))",
        (game_id, cfg.depth, cfg.movetime_ms or None, game_id),
    )
    con.commit()


# ---------- reading ----------

def latest_runs(con: sqlite3.Connection, game_id: int) -> tuple[sqlite3.Row | None, sqlite3.Row | None]:
    """(latest done run, latest run if it is still queued/running/failed)."""
    latest = con.execute("SELECT * FROM analysis_runs WHERE game_id = ? ORDER BY id DESC LIMIT 1", (game_id,)).fetchone()
    if latest is None:
        return None, None
    if latest["status"] == "done":
        return latest, None
    done = con.execute(
        "SELECT * FROM analysis_runs WHERE game_id = ? AND status = 'done' ORDER BY id DESC LIMIT 1", (game_id,)
    ).fetchone()
    return done, latest


def _san_line(fen: str, ucis: list[str]) -> list[dict]:
    board = chess.Board(fen)
    line = []
    for uci in ucis:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        line.append({"san": board.san(move), "uci": uci, "fen": None})
        board.push(move)
        line[-1]["fen"] = board.fen()
    return line


def _eval_label(row: sqlite3.Row) -> str:
    if row["cp"] is None and row["mate"] is None:
        return "Draw" if row["win"] == 50 else "Mate"
    return review.fmt_eval(row["cp"], row["mate"])


def _run_dict(run: sqlite3.Row | None) -> dict | None:
    if run is None:
        return None
    keys = ("id", "status", "engine", "depth", "movetime_ms", "progress", "total", "white_accuracy",
            "black_accuracy", "error", "created_at", "finished_at")
    return {k: run[k] for k in keys}


def load_review(con: sqlite3.Connection, game_id: int) -> dict:
    done, pending = latest_runs(con, game_id)
    result = {"run": _run_dict(done), "pending": _run_dict(pending), "positions": [], "moves": [],
              "coach_summary": db.kv_get(con, summary_key(game_id))}
    user_color = con.execute("SELECT user_color FROM games WHERE id = ?", (game_id,)).fetchone()[0]
    if done is None:
        return result
    rows = con.execute("SELECT * FROM move_evals WHERE run_id = ? ORDER BY ply", (done["id"],)).fetchall()
    fens = [r["fen"] for r in rows]
    found = con.execute(
        f"SELECT fen, move_uci, text, source FROM explanations WHERE fen IN ({', '.join('?' * len(fens))})", fens
    ).fetchall()
    imported = {(r["fen"], r["move_uci"]): r for r in found}
    move_tags: dict[int, list] = {}
    for t in con.execute("SELECT ply, tag, kind FROM move_tags WHERE run_id = ? ORDER BY kind, tag", (done["id"],)):
        move_tags.setdefault(t["ply"], []).append({"tag": t["tag"], "kind": t["kind"], "label": tactics.LABELS[t["tag"]]})

    result["positions"] = [{"ply": r["ply"], "win": r["win"], "eval": _eval_label(r)} for r in rows]
    counts = {c: {k: 0 for k in review.CLASSES} for c in ("white", "black")}
    trouble = {"white": 0, "black": 0}
    for before, after in zip(rows, rows[1:]):
        board = chess.Board(before["fen"])
        best, reply = before["best_uci"], after["best_uci"]
        prev_own = rows[after["ply"] - 2] if after["ply"] >= 2 else None  # mover's previous move
        info = {
            "ply": after["ply"],
            "san": after["san"],
            "uci": after["move_uci"],
            "color": after["color"],
            "classification": after["classification"],
            "win_before": before["win"],
            "win_after": after["win"],
            "win_drop": after["win_drop"],
            "accuracy": after["accuracy"],
            "eval_before": _eval_label(before),
            "eval_after": _eval_label(after),
            "mate_before": before["mate"],
            "mate_after": after["mate"],
            "best_uci": best,
            "best_san": board.san(chess.Move.from_uci(best)) if best else None,
            "best_line": _san_line(before["fen"], (before["pv"] or "").split()),
            "refutation_san": chess.Board(after["fen"]).san(chess.Move.from_uci(reply)) if reply else None,
            "next_best_uci": reply,
            "clock": after["clock"],
            "clock_before": prev_own["clock"] if prev_own else None,
            "time_spent": after["time_spent"],
            "time_trouble": bool(after["time_trouble"]),
            "fen_before": before["fen"],
            "material_loss": review.material_loss(before["fen"], after["move_uci"], (after["pv"] or "").split()),
            "is_user": after["color"] == user_color,
            "phase": after["phase"],
            "tags": move_tags.get(after["ply"], []),
        }
        exp = imported.get((before["fen"], after["move_uci"]))
        info["explanation"] = exp["text"] if exp else review.explain(info)
        info["explanation_source"] = exp["source"] if exp else "template"
        counts[after["color"]][after["classification"]] += 1
        trouble[after["color"]] += int(after["time_trouble"])
        result["moves"].append(info)
    result["run"]["counts"] = counts
    result["run"]["time_trouble"] = trouble
    return result


# ---------- LLM batch export / import ----------

LLM_TASK = {
    "game": "For every move below, explain in 1-2 short sentences what it does: the idea behind good moves, and for "
            "inaccuracies, mistakes and blunders why they were wrong and what the engine's move achieves.",
    "key": "For each key moment below, explain in 2-3 short sentences why the move played was an error and what "
           "the engine's better move achieves.",
}
LLM_HEADER = """# Chezz: explanation batch

You are a friendly chess coach for a club player rated about 1200.{player}
{task} Use plain language and name concrete ideas (hanging piece, fork, pin, skewer, back-rank,
discovered attack, missed mate) when they apply. Only rely on the engine data given: it is exact,
so do not work out moves or positions yourself.

Reply in exactly this format. First a summary of the game in 3-5 sentences (how it was decided and
the one habit worth working on), then one block per position, copying each KEY line unchanged:

### SUMMARY {game_id}
<your summary>

### KEY <fen>|<move>
<your explanation>

"""


def _clock(sec: float | None) -> str:
    if sec is None:
        return "?"
    sec = max(0.0, sec)
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def llm_batch(con: sqlite3.Connection, game: sqlite3.Row, scope: str = "key") -> tuple[str, int]:
    """Markdown for any chat assistant: scope 'game' = every move, 'key' = inaccuracies, mistakes and blunders."""
    data = load_review(con, game["id"])
    moves = data["moves"]
    picked = moves if scope == "game" else [m for m in moves if m["classification"] in ERROR_CLASSES]
    side = game["user_color"]
    player = f" They played {side} ({game[side]}) in this game." if side else ""
    label = lambda m: f"{(m['ply'] + 1) // 2}{'.' if m['color'] == 'white' else '...'}"
    movetext = " ".join(f"{label(m)} {m['san']}" if m["color"] == "white" or i == 0 else m["san"] for i, m in enumerate(moves))
    out = [
        LLM_HEADER.format(player=player, task=LLM_TASK[scope], game_id=game["id"]),
        f"Game: {game['white']} (White) vs {game['black']} (Black), {game['date'] or 'date unknown'}, result {game['result']}",
        f"Opening: {game['opening'] or 'unknown'}",
        f"Moves: {movetext}\n",
    ]
    for m in picked:
        line = " ".join(x["san"] for x in m["best_line"][:6])
        rows = [
            f"- Move: {label(m)} {m['san']} by {m['color']} ({m['classification']}"
            + (f", {m['phase']}" if m["phase"] else "") + ")",
            f"- Win chance for {m['color']}: {review.mover_view(m['win_before'], m['color']):.0f}% before, "
            f"{review.mover_view(m['win_after'], m['color']):.0f}% after",
            f"- Eval before: {m['eval_before']}, after: {m['eval_after']} (White's view)",
        ]
        if m["best_uci"] and m["best_uci"] != m["uci"]:
            rows.append(f"- Engine best: {m['best_san']}, line: {line}")
        if m["classification"] in ERROR_CLASSES and m["refutation_san"]:
            rows.append(f"- Engine reply to the move played: {m['refutation_san']}")
        if m["tags"]:
            rows.append("- Tactics: " + "; ".join(
                f"{'allows' if t['kind'] == 'allowed' else 'missed'} {t['label'].lower()}" for t in m["tags"]))
        if m["time_spent"] is not None:
            rows.append(f"- Time: spent {_clock(m['time_spent'])}, {_clock(m['clock'])} left"
                        + (" (time trouble)" if m["time_trouble"] else ""))
        out.append(f"---\n\n### KEY {m['fen_before']}|{m['uci']}\n\n" + "\n".join(rows) + "\n")
    return "\n".join(out), len(picked)


HEAD_RE = re.compile(
    r"^#{1,6}\s*(?:KEY\s+(?P<fen>.+?)\s*\|\s*(?P<uci>[a-h][1-8][a-h][1-8][qrbn]?)|SUMMARY\s+(?P<game>\d+))\s*$",
    re.MULTILINE)


def _blocks(text: str):
    """(header match, body) for every '### KEY fen|uci' and '### SUMMARY <game id>' block."""
    matches = list(HEAD_RE.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # A block ends at the next header or at a '---' separator line (assistants often chat after one).
        body = re.split(r"\n[ \t]*-{3,}[ \t]*(?:\n|$)", text[m.end():end].strip() + "\n")[0].strip()
        if body:
            yield m, body


def parse_explanations(text: str) -> list[tuple[str, str, str]]:
    """Parse '### KEY fen|uci' blocks from an LLM reply into (fen, uci, explanation)."""
    return [(m["fen"].strip(), m["uci"], body) for m, body in _blocks(text) if m["fen"]]


def parse_summaries(text: str) -> list[tuple[int, str]]:
    return [(int(m["game"]), body) for m, body in _blocks(text) if m["game"]]


def summary_key(game_id: int) -> str:
    return f"coach_summary:{game_id}"


def import_explanations(con: sqlite3.Connection, text: str) -> dict:
    items = parse_explanations(text)
    con.executemany(
        "INSERT INTO explanations (fen, move_uci, text, source) VALUES (?, ?, ?, 'llm') "
        "ON CONFLICT (fen, move_uci) DO UPDATE SET text = excluded.text, source = 'llm', created_at = datetime('now')",
        items,
    )
    summaries = [(gid, body) for gid, body in parse_summaries(text)
                 if con.execute("SELECT 1 FROM games WHERE id = ?", (gid,)).fetchone()]
    for gid, body in summaries:
        db.kv_set(con, summary_key(gid), body)
    con.commit()
    return {"imported": len(items), "summaries": len(summaries)}
