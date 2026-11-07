import chess
import pytest

from chezz import analysis, review


def test_win_percent_shape():
    assert review.win_percent(0, None) == pytest.approx(50)
    assert review.win_percent(100, None) == pytest.approx(59.1, abs=0.1)  # +1 pawn ≈ 59%
    assert review.win_percent(-100, None) == pytest.approx(100 - review.win_percent(100, None))
    assert review.win_percent(5000, None) == review.win_percent(1000, None)  # capped at ±10 pawns
    assert review.win_percent(None, 3) == 100 and review.win_percent(None, -1) == 0
    xs = [review.win_percent(cp, None) for cp in range(-1000, 1001, 50)]
    assert xs == sorted(xs)


@pytest.mark.parametrize(
    "before, after, color, expected",
    [
        (50, 49.5, "white", "best"),        # < 1% loss counts as best
        (50, 46, "white", "good"),          # 4%
        (50, 45, "white", "inaccuracy"),    # exactly 5%
        (50, 40.5, "white", "inaccuracy"),
        (50, 40, "white", "mistake"),       # 10%
        (50, 35.1, "white", "mistake"),
        (50, 35, "white", "blunder"),       # 15%
        (50, 60, "black", "mistake"),       # black's view: 50 -> 40
        (50, 70, "white", "best"),          # gaining is never an error
    ],
)
def test_classify_thresholds(before, after, color, expected):
    assert review.classify(before, after, color, "e2e4", "d2d4")[0] == expected


def test_engine_move_is_best_even_if_eval_drops():
    # Engine's own move can show a drop due to search noise; it is still "best".
    assert review.classify(60, 50, "white", "g1f3", "g1f3") == ("best", 10)


def test_move_accuracy():
    assert review.move_accuracy(50, 50, "white") == 100
    assert review.move_accuracy(50, 60, "white") == 100
    assert 60 < review.move_accuracy(50, 40, "white") < 70  # 10% loss ≈ 65
    assert review.move_accuracy(100, 0, "white") == 0
    assert review.move_accuracy(50, 40, "black") == 100  # black gained


def test_game_accuracy():
    perfect = [50.0] * 41
    assert review.game_accuracy(perfect) == {"white": 100, "black": 100}

    # White blunders once (ply 10: 55 -> 20), everything else flat.
    wins = [55.0] * 11 + [20.0] * 30
    acc = review.game_accuracy(wins)
    assert acc["black"] == 100
    assert 60 < acc["white"] < 95

    worse = [55.0] * 11 + [20.0] * 10 + [5.0] * 20  # second blunder
    assert review.game_accuracy(worse)["white"] < acc["white"]

    assert review.game_accuracy([50.0]) == {"white": None, "black": None}
    # Game starting with black to move: first move belongs to black.
    one = review.game_accuracy([50.0, 80.0], start_white=False)
    assert one["white"] is None and 20 < one["black"] < 30


def test_clock_data_time_trouble():
    # 60s bullet: threshold 10s. White: 55, 30, 9 -> moves made with 60, 55, 30, 9 left.
    clocks = [55, 58, 30, 50, 9, 45, 5, 40]
    data = review.clock_data(clocks, "60")
    assert [d["time_spent"] for d in data[:2]] == [5, 2]
    assert [d["time_trouble"] for d in data] == [False, False, False, False, False, False, True, False]
    assert data[6]["clock_before"] == 9

    with_inc = review.clock_data([175, 170], "180+2")
    assert with_inc[0]["time_spent"] == 7  # 180 - 175 + 2

    assert review.time_trouble_threshold(600) == 60
    assert review.time_trouble_threshold(60) == 10
    assert review.time_trouble_threshold(180) == 18
    assert all(not d["time_trouble"] for d in review.clock_data([None, None], None))


def _move(**kw):
    base = dict(san="Qxd5", color="white", classification="blunder", win_before=62, win_after=18, best_san="Nf3",
                refutation_san="Bxd5", mate_before=None, mate_after=None, eval_before="+0.4", time_trouble=False,
                clock_before=None, material_loss=9)
    base.update(kw)
    return base


def test_explain_templates():
    text = review.explain(_move())
    assert "blunder" in text and "62% to 18%" in text and "Bxd5" in text and "Nf3" in text
    assert "After Bxd5 you lose the queen." in text
    assert "keeping your winning chances around 62%" in text
    # A plain recapture is not described as losing material.
    assert "The strongest reply is Bxd5." in review.explain(_move(material_loss=0))
    assert "you lose a piece" in review.explain(_move(material_loss=3))
    # Opponent's moves are described in the third person.
    opp = review.explain(_move(is_user=False, color="black", win_before=38, win_after=82))
    assert "Black's winning chances went from 62% to 18%" in opp
    assert "After Bxd5 Black loses the queen." in opp
    assert "Black had mate in 3" in review.explain(_move(is_user=False, color="black", mate_before=-3, win_before=0, win_after=20))


def test_material_loss():
    board = chess.Board()
    for san in ["e4", "e5", "Qh5", "Nc6"]:
        board.push_san(san)
    # 3. Qxf7+?? Kxf7: queen for a pawn.
    assert review.material_loss(board.fen(), "h5f7", ["e8f7"]) == 8
    # 1. e4 d5 2. exd5 Qxd5: even trade.
    b2 = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 2")
    assert review.material_loss(b2.fen(), "e4d5", ["d8d5"]) == 0
    # Illegal/short lines are cut off safely.
    assert review.material_loss(b2.fen(), "e4d5", ["a1a8"]) == -1

    assert "allows mate in 2" in review.explain(_move(mate_after=-2, refutation_san="Qh4+"))
    missed = review.explain(_move(mate_before=3, mate_after=None, win_before=100, win_after=80))
    assert "You had mate in 3" in missed
    # Black's perspective: White-POV 38% before -> 82% after means black went 62% -> 18%.
    assert "62% to 18%" in review.explain(_move(color="black", win_before=38, win_after=82))
    assert "0:08 left" in review.explain(_move(time_trouble=True, clock_before=8.4))
    assert review.explain(_move(classification="best", best_san="Qxd5")) == "Qxd5 is the best move."


def test_build_rows_classifies_and_flags():
    fens = ["f0", "f1", "f2", "f3"]
    moves = [("e2e4", "e4"), ("d7d5", "d5"), ("e4d5", "exd5")]
    evals = [
        {"cp": 20, "mate": None, "win": 52.0, "best": "e2e4", "pv": ["e2e4", "e7e5"]},
        {"cp": 30, "mate": None, "win": 53.0, "best": "e7e5", "pv": ["e7e5"]},
        {"cp": 600, "mate": None, "win": 90.0, "best": "e4d5", "pv": ["e4d5"]},
        {"cp": 600, "mate": None, "win": 90.0, "best": "d8d5", "pv": ["d8d5"]},
    ]
    rows = analysis.build_rows(fens, moves, evals, [55, 50, 9], "60")
    assert [r["classification"] for r in rows] == [None, "best", "blunder", "best"]
    assert rows[2]["color"] == "black" and rows[2]["win_drop"] == 37
    assert rows[0]["pv"] == "e2e4 e7e5"
    assert rows[3]["time_trouble"] == 0  # white had 55s when thinking


LLM_REPLY = """Sure! Here are the explanations.

### KEY r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3|f3e5
Taking on e5 drops the knight: after Nxe5 Nxe5 you have lost a piece for a pawn.

---

### KEY 8/8/8/8/8/8/8/K6k b - - 0 1 | h1g2
Line one.
Line two.
"""


def test_parse_explanations():
    items = analysis.parse_explanations(LLM_REPLY)
    assert len(items) == 2
    fen, uci, text = items[0]
    assert fen.startswith("r1bqkbnr/") and fen.endswith("2 3") and uci == "f3e5"
    assert text.startswith("Taking on e5") and "---" not in text
    assert items[1][1:] == ("h1g2", "Line one.\nLine two.")
    assert analysis.parse_explanations("no keys here") == []


def test_parse_summary_blocks():
    reply = "### SUMMARY 7\nYou won the opening, then hung a rook.\n\n---\n\n" + LLM_REPLY
    assert analysis.parse_summaries(reply) == [(7, "You won the opening, then hung a rook.")]
    assert len(analysis.parse_explanations(reply)) == 2  # summary text never leaks into a move
    assert analysis.parse_summaries(LLM_REPLY) == []
