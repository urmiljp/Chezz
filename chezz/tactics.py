"""Tactic tagging and game phase with python-chess heuristics.

`motifs(board, move)` describes what a move does for the side playing it. For an error move we tag
  - 'allowed': motifs of the opponent's best reply (how the error gets punished), and
  - 'missed':  motifs of the engine's better move (what the player overlooked).
"""
import sqlite3

import chess

VERSION = "2"  # bump when the heuristics change: finished runs are re-tagged at startup
TAGS = ("hanging_piece", "fork", "pin", "skewer", "back_rank", "discovered_attack", "missed_mate")
LABELS = {
    "hanging_piece": "Hanging piece",
    "fork": "Fork",
    "pin": "Pin",
    "skewer": "Skewer",
    "back_rank": "Back-rank mate",
    "discovered_attack": "Discovered attack",
    "missed_mate": "Missed mate",
}
# Lichess puzzle theme for each tag (used to pick matching puzzles and lessons).
PUZZLE_THEME = {
    "hanging_piece": "hangingPiece",
    "fork": "fork",
    "pin": "pin",
    "skewer": "skewer",
    "back_rank": "backRankMate",
    "discovered_attack": "discoveredAttack",
    "missed_mate": "mate",
}
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
SLIDER_DIRS = {
    chess.BISHOP: [(1, 1), (1, -1), (-1, 1), (-1, -1)],
    chess.ROOK: [(1, 0), (-1, 0), (0, 1), (0, -1)],
}
SLIDER_DIRS[chess.QUEEN] = SLIDER_DIRS[chess.BISHOP] + SLIDER_DIRS[chess.ROOK]
ERRORS = ("inaccuracy", "mistake", "blunder")


def phase(board: chess.Board) -> str:
    """'endgame' once both sides together have <= 26 points of pieces, 'opening' up to move 12."""
    pieces = sum(VALUE[p.piece_type] for p in board.piece_map().values() if p.piece_type not in (chess.PAWN, chess.KING))
    if pieces <= 26:
        return "endgame"
    return "opening" if board.fullmove_number <= 12 else "middlegame"


def _is_target(board: chess.Board, sq: int, victim: chess.Color, attacker_value: int) -> bool:
    """A piece worth attacking: the king, something worth more than the attacker, or an undefended piece."""
    p = board.piece_at(sq)
    if p is None or p.color != victim or p.piece_type == chess.PAWN:
        return False
    return p.piece_type == chess.KING or VALUE[p.piece_type] > attacker_value or not board.is_attacked_by(victim, sq)


def _pinned(board: chess.Board, color: chess.Color) -> set[int]:
    return {sq for sq, p in board.piece_map().items()
            if p.color == color and p.piece_type not in (chess.PAWN, chess.KING) and board.is_pinned(color, sq)}


def _skewer(board: chess.Board, sq: int, victim: chess.Color) -> bool:
    """Slider on `sq` attacks a valuable piece with a lesser (but real) piece behind it on the same line."""
    piece = board.piece_at(sq)
    for df, dr in SLIDER_DIRS.get(piece.piece_type, []):
        f, r = chess.square_file(sq), chess.square_rank(sq)
        found = []
        while len(found) < 2:
            f, r = f + df, r + dr
            if not (0 <= f < 8 and 0 <= r < 8):
                break
            p = board.piece_at(chess.square(f, r))
            if p is None:
                continue
            if p.color != victim:
                break
            found.append(p)
        if len(found) == 2:
            front, behind = found
            if front.piece_type in (chess.KING, chess.QUEEN) and VALUE[behind.piece_type] >= 3 \
                    and VALUE[front.piece_type] > VALUE[behind.piece_type]:
                return True
    return False


def _back_rank_mate(board: chess.Board, line: list[chess.Move]) -> bool:
    b = board.copy(stack=False)
    for move in line:
        if move not in b.legal_moves:
            return False
        b.push(move)
        if b.is_checkmate():
            king = b.king(b.turn)
            back = 0 if b.turn == chess.WHITE else 7
            return chess.square_rank(king) == back and any(
                b.piece_at(s).piece_type in (chess.ROOK, chess.QUEEN) and chess.square_rank(s) == back
                for s in b.checkers()
            )
    return False


def motifs(board: chess.Board, move: chess.Move, line: list[chess.Move] | None = None) -> set[str]:
    """Tactical motifs of `move` for the side to move on `board`. `line` = move + engine continuation."""
    tags: set[str] = set()
    attacker, victim = board.turn, not board.turn
    piece = board.piece_at(move.from_square)
    captured = board.piece_at(move.to_square)
    after = board.copy(stack=False)
    after.push(move)

    # En prise: an undefended piece, or one taken by a cheaper piece (e.g. pawn takes knight).
    if captured and captured.piece_type != chess.PAWN and (
        not board.is_attacked_by(victim, move.to_square) or VALUE[captured.piece_type] > VALUE[piece.piece_type]
    ):
        tags.add("hanging_piece")

    if not after.is_checkmate():
        value = VALUE[piece.piece_type]
        targets = [s for s in after.attacks(move.to_square) if _is_target(after, s, victim, value)]
        attacked = after.is_attacked_by(victim, move.to_square)
        cheaper_attacker = any(VALUE[after.piece_at(s).piece_type] < value for s in after.attackers(victim, move.to_square))
        safe = not attacked or (after.is_attacked_by(attacker, move.to_square) and not cheaper_attacker)
        if len(targets) >= 2 and (safe or after.is_check()):
            tags.add("fork")

        for sq, p in after.piece_map().items():
            if p.color == attacker and sq != move.to_square and p.piece_type in SLIDER_DIRS:
                new = after.attacks(sq) - board.attacks(sq)
                if any(_is_target(after, s, victim, VALUE[p.piece_type]) for s in new):
                    tags.add("discovered_attack")
                    break

        if any(VALUE[after.piece_at(s).piece_type] >= 3 for s in _pinned(after, victim) - _pinned(board, victim)):
            tags.add("pin")

        if piece.piece_type in SLIDER_DIRS and _skewer(after, move.to_square, victim):
            tags.add("skewer")

    if _back_rank_mate(board, line or [move]):
        tags.add("back_rank")
    return tags


def _moves(ucis: str | None) -> list[chess.Move]:
    return [chess.Move.from_uci(u) for u in (ucis or "").split()]


def tag_run(con: sqlite3.Connection, run_id: int) -> None:
    """Store phase for every move and tactic tags for every error move of a finished run."""
    rows = con.execute("SELECT * FROM move_evals WHERE run_id = ? ORDER BY ply", (run_id,)).fetchall()
    con.execute("DELETE FROM move_tags WHERE run_id = ?", (run_id,))
    tags = []
    for before, after in zip(rows, rows[1:]):
        board = chess.Board(before["fen"])
        con.execute("UPDATE move_evals SET phase = ? WHERE run_id = ? AND ply = ?", (phase(board), run_id, after["ply"]))
        if after["classification"] not in ERRORS:
            continue
        sign = 1 if after["color"] == "white" else -1
        if (before["mate"] or 0) * sign > 0 and (after["mate"] or 0) * sign <= 0:
            tags.append((after["ply"], "missed_mate", "missed"))
        best = _moves(before["pv"])
        if best and best[0].uci() != after["move_uci"]:
            tags += [(after["ply"], t, "missed") for t in motifs(board, best[0], best)]
        reply = _moves(after["pv"])
        if reply:
            board_after = chess.Board(after["fen"])
            if reply[0] in board_after.legal_moves:
                tags += [(after["ply"], t, "allowed") for t in motifs(board_after, reply[0], reply)]
    con.executemany(
        "INSERT OR IGNORE INTO move_tags (run_id, ply, tag, kind) VALUES (?, ?, ?, ?)",
        [(run_id, *t) for t in tags],
    )
    con.execute("UPDATE analysis_runs SET tagged = 1 WHERE id = ?", (run_id,))
    con.commit()
