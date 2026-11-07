"""Pure review math: win probability, move classification, accuracy, time trouble, text templates.

All evals are stored from White's point of view; helpers convert to the mover's view.
Formulas follow Lichess (win% from centipawns, accuracy from win% loss, volatility-weighted game accuracy).
"""
import math
import re
from statistics import pstdev

import chess

CLASSES = ("best", "good", "inaccuracy", "mistake", "blunder")
# Minimum drop in the mover's win% (0-100) for each class.
INACCURACY, MISTAKE, BLUNDER = 5.0, 10.0, 15.0
BEST_TOLERANCE = 1.0  # a different move that loses less than this is still "best"


def win_percent(cp: int | None, mate: int | None) -> float:
    """White's winning chances in % from a White-POV centipawn score or mate distance."""
    if mate is not None:
        return 100.0 if mate > 0 else 0.0
    cp = max(-1000, min(1000, cp or 0))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def mover_view(win_white: float, color: str) -> float:
    return win_white if color == "white" else 100 - win_white


def classify(win_before: float, win_after: float, color: str, played: str, best: str | None) -> tuple[str, float]:
    """Return (classification, drop) where drop is the mover's lost win% (>= 0)."""
    drop = max(0.0, mover_view(win_before, color) - mover_view(win_after, color))
    if played == best or drop < BEST_TOLERANCE:
        return "best", drop
    if drop < INACCURACY:
        return "good", drop
    if drop < MISTAKE:
        return "inaccuracy", drop
    if drop < BLUNDER:
        return "mistake", drop
    return "blunder", drop


def move_accuracy(win_before: float, win_after: float, color: str) -> float:
    drop = max(0.0, mover_view(win_before, color) - mover_view(win_after, color))
    acc = 103.1668100711649 * math.exp(-0.04354415386753951 * drop) - 3.166924740191411 + 1  # +1 uncertainty bonus
    return max(0.0, min(100.0, acc))


def game_accuracy(wins: list[float], start_white: bool = True) -> dict[str, float | None]:
    """Per-side accuracy from White-POV win% of every position (start position first).

    Mean of a volatility-weighted average and a harmonic mean, as Lichess does: errors in calm
    positions weigh more, and a single blunder can't be hidden by many easy moves.
    """
    n_moves = len(wins) - 1
    if n_moves < 1:
        return {"white": None, "black": None}
    window = max(2, min(8, len(wins) // 10))
    windows = [wins[:window]] * max(0, min(window, len(wins)) - 2)
    windows += [wins[i : i + window] for i in range(len(wins) - window + 1)]
    weights = [max(0.5, min(12.0, pstdev(w))) for w in windows][:n_moves]

    out: dict[str, float | None] = {}
    for color in ("white", "black"):
        accs, ws = [], []
        for i in range(n_moves):
            mover = "white" if (i % 2 == 0) == start_white else "black"
            if mover == color:
                accs.append(move_accuracy(wins[i], wins[i + 1], color))
                ws.append(weights[i])
        if not accs:
            out[color] = None
            continue
        weighted = sum(a * w for a, w in zip(accs, ws)) / sum(ws)
        harmonic = len(accs) / sum(1 / max(a, 1.0) for a in accs)
        out[color] = round((weighted + harmonic) / 2, 1)
    return out


def parse_time_control(tc: str | None) -> tuple[float | None, float]:
    m = re.fullmatch(r"(\d+)(?:\+(\d+(?:\.\d+)?))?", (tc or "").strip())
    return (float(m[1]), float(m[2] or 0)) if m else (None, 0.0)


def time_trouble_threshold(base: float) -> float:
    """Seconds below which a player is 'in time trouble': 10% of base time, between 10s and 60s."""
    return max(10.0, min(60.0, base * 0.1))


def clock_data(clocks: list[float | None], tc: str | None, start_white: bool = True) -> list[dict]:
    """Per move: time spent and whether the mover started thinking in time trouble."""
    base, inc = parse_time_control(tc)
    if base is None:
        base = next((c for c in clocks if c is not None), None)
    out = []
    last = {"white": base, "black": base}
    threshold = time_trouble_threshold(base) if base else None
    for i, clock in enumerate(clocks):
        mover = "white" if (i % 2 == 0) == start_white else "black"
        before = last[mover]
        spent = None
        if clock is not None and before is not None:
            spent = round(max(0.0, before - clock + inc), 1)
        trouble = bool(threshold and before is not None and clock is not None and before < threshold)
        out.append({"clock": clock, "clock_before": before, "time_spent": spent, "time_trouble": trouble})
        if clock is not None:
            last[mover] = clock
    return out


def fmt_eval(cp: int | None, mate: int | None) -> str:
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{abs(mate)}"
    return f"{(cp or 0) / 100:+.1f}"


def fmt_clock(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


PIECE_VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9, 6: 0}  # chess.PAWN..chess.KING


def material_loss(fen_before: str, move_uci: str, reply_line: list[str], plies: int = 4) -> int:
    """Material the mover is down after their move plus the engine's best reply line (4 plies ends
    on the mover's own recapture). Positive = material lost."""
    board = chess.Board(fen_before)
    me = board.turn

    def balance() -> int:
        return sum(PIECE_VALUES[p.piece_type] * (1 if p.color == me else -1) for p in board.piece_map().values())

    start = balance()
    board.push(chess.Move.from_uci(move_uci))
    for uci in reply_line[:plies]:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        board.push(move)
    return start - balance()


def describe_loss(points: int) -> str:
    if points >= 8:
        return "the queen"
    if points >= 5:
        return "a rook"
    if points >= 3:
        return "a piece"
    return "two pawns" if points == 2 else "a pawn"


def explain(m: dict) -> str:
    """Template explanation from engine data for one analysed move.

    Expects keys: san, color, classification, win_before, win_after (White POV), best_san, refutation_san,
    mate_before/mate_after (White POV), material_loss, time_trouble, clock_before, is_user (default True).
    """
    me = m["color"]
    sign = 1 if me == "white" else -1
    before = round(mover_view(m["win_before"], me))
    after = round(mover_view(m["win_after"], me))
    cls = m["classification"]
    best = m.get("best_san")
    mate_before = (m.get("mate_before") or 0) * sign
    mate_after = (m.get("mate_after") or 0) * sign
    refute = m.get("refutation_san")
    # Speak to the player about their own moves, in the third person about the opponent's.
    you = m.get("is_user", True)
    side = me.capitalize()
    poss, had, loses = ("your", "You had", "you lose") if you else (f"{side}'s", f"{side} had", f"{side} loses")
    parts = []

    if cls == "best":
        parts.append(f"{m['san']} is the best move." if best in (None, m["san"]) else f"{m['san']} is as good as the engine's {best}.")
    elif cls == "good":
        parts.append(f"{m['san']} is a good move." + (f" {best} was slightly more precise." if best else ""))
    else:
        label = {"inaccuracy": "an inaccuracy", "mistake": "a mistake", "blunder": "a blunder"}[cls]
        parts.append(f"{m['san']} is {label}: {poss} winning chances went from {before}% to {after}%.")
        if mate_before > 0 and mate_after <= 0:
            parts.append(f"{had} mate in {mate_before} starting with {best}.")
        elif mate_after < 0:
            parts.append(f"It allows mate in {abs(mate_after)}" + (f", starting with {refute}." if refute else "."))
        elif refute and m.get("material_loss", 0) >= 1:
            parts.append(f"After {refute} {loses} {describe_loss(m['material_loss'])}.")
        elif refute:
            parts.append(f"The strongest reply is {refute}.")
        if best and not (mate_before > 0 and mate_after <= 0):
            parts.append(f"Better was {best}, keeping {poss} winning chances around {before}%.")
    if m.get("time_trouble") and m.get("clock_before") is not None and cls in ("inaccuracy", "mistake", "blunder"):
        parts.append(f"Played with only {fmt_clock(m['clock_before'])} left on the clock.")
    return " ".join(parts)
