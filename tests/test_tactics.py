import chess
import pytest

from chezz import db, tactics, weakness


def tags(fen: str, uci: str, line: list[str] | None = None) -> set[str]:
    board = chess.Board(fen)
    move = chess.Move.from_uci(uci)
    return tactics.motifs(board, move, [chess.Move.from_uci(u) for u in (line or [uci])])


def test_fork():
    assert "fork" in tags("r3k3/8/8/1N6/8/8/5PPP/6K1 w - - 0 1", "b5c7")
    # A quiet knight move is not a fork.
    assert "fork" not in tags("r3k3/8/8/1N6/8/8/5PPP/6K1 w - - 0 1", "b5d4")


def test_pawn_fork_trick():
    assert "fork" in tags("r1bqkb1r/pppp1ppp/2n5/4p3/2B1N3/5N2/PPPP1PPP/R1BQK2R b KQkq - 0 5", "d7d5")


def test_hanging_piece():
    assert "hanging_piece" in tags("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1", "d1d5")
    # Capturing a defended piece with a bigger piece is not "hanging".
    assert "hanging_piece" not in tags("4k3/4p3/3n4/8/8/8/8/3QK3 w - - 0 1", "d1d6")
    # ...but a defended piece taken by a cheaper one was still en prise.
    assert "hanging_piece" in tags("4k3/4p3/3n4/2P5/8/8/8/4K3 w - - 0 1", "c5d6")


def test_pin_and_skewer():
    assert "pin" in tags("4k3/4q3/8/8/8/8/5PPP/R4K2 w - - 0 1", "a1e1")
    assert "skewer" in tags("8/4q3/8/8/8/4k3/8/R5K1 w - - 0 1", "a1e1")
    assert "skewer" in tags("6r1/8/8/3k4/8/8/6PP/3BK3 w - - 0 1", "d1b3")
    assert "pin" not in tags("8/4q3/8/8/8/4k3/8/R5K1 w - - 0 1", "a1e1")


def test_discovered_attack():
    assert "discovered_attack" in tags("4q1k1/6pp/8/8/4B3/8/5PPP/4R1K1 w - - 0 1", "e4h7")
    assert "discovered_attack" in tags("3k4/8/4q3/8/8/3N4/5PPP/3R2K1 w - - 0 1", "d3c5")
    assert "discovered_attack" not in tags("3k4/8/4q3/8/8/3N4/5PPP/3R2K1 w - - 0 1", "g2g3")


def test_back_rank():
    assert "back_rank" in tags("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "a1a8")
    assert "back_rank" in tags("6k1/5ppp/8/8/8/8/1r3PPP/R5K1 w - - 0 1", "a1a8", ["a1a8", "b2b8", "a8b8"])
    # Mate on the edge but not by a rook/queen on the back rank.
    assert "back_rank" not in tags("k7/8/1K6/8/8/8/7Q/8 w - - 0 1", "h2b8")


@pytest.mark.parametrize("fen, expected", [
    (chess.STARTING_FEN, "opening"),
    ("8/8/8/4k3/8/8/8/R3K3 w - - 0 1", "endgame"),
    ("r1bq1rk1/pp2bppp/2n1pn2/3p4/3P4/2NBPN2/PP3PPP/R2Q1RK1 w - - 0 15", "middlegame"),
])
def test_phase(fen, expected):
    assert tactics.phase(chess.Board(fen)) == expected


def _seed_run(con, rows, user_color="white"):
    con.execute("INSERT INTO games (id, dedupe_key, pgn, user_color, white, black, result) VALUES (1, 'k', '', ?, 'a', 'b', '1-0')", (user_color,))
    con.execute("INSERT INTO analysis_runs (id, game_id, status) VALUES (1, 1, 'done')")
    for r in rows:
        con.execute(
            "INSERT INTO move_evals (run_id, ply, fen, move_uci, color, cp, mate, win, best_uci, pv, classification, accuracy) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", r)
    con.commit()


def test_tag_run_and_weakness_profile(con):
    # White to move could fork (Nc7+) but plays Kf1 instead; that is the error at ply 1.
    start = "r3k3/8/8/1N6/8/8/5PPP/6K1 w - - 0 1"
    after = "r3k3/8/8/1N6/8/8/5PPP/5K2 b - - 1 1"
    _seed_run(con, [
        (0, start, None, None, 700, None, 80.0, "b5c7", "b5c7 e8f7 c7a8", None, None),
        (1, after, "g1f1", "white", 50, None, 54.0, "a8a5", "a8a5", "blunder", 30.0),
    ])
    tactics.tag_run(con, 1)
    found = {(r["tag"], r["kind"]) for r in con.execute("SELECT tag, kind FROM move_tags")}
    assert ("fork", "missed") in found
    assert con.execute("SELECT phase FROM move_evals WHERE ply = 1").fetchone()[0] == "endgame"
    assert con.execute("SELECT tagged FROM analysis_runs WHERE id = 1").fetchone()[0] == 1

    p = weakness.profile(con)
    assert p["summary"]["games"] == 1 and p["summary"]["blunders"] == 1
    fork = next(t for t in p["tactics"] if t["tag"] == "fork")
    assert fork["missed"] == 1 and fork["theme"] == "fork"
    assert p["weaknesses"][0]["key"] == "tag:fork"


def test_ranked_orders_by_cost():
    prof = {
        "summary": {"games": 10, "moves": 400, "errors": 40},
        "tactics": [
            {"tag": "fork", "label": "Fork", "theme": "fork", "allowed": 3, "missed": 2},
            {"tag": "pin", "label": "Pin", "theme": "pin", "allowed": 12, "missed": 4},
        ],
        "phases": [{"phase": "endgame", "moves": 100, "errors": 25}, {"phase": "opening", "moves": 100, "errors": 5}],
        "time": {"trouble": {"moves": 20, "errors": 10}},
        "openings": [{"name": "Sicilian Defense", "games": 5, "score": 20}],
    }
    keys = [w["key"] for w in weakness.ranked(prof)]
    assert keys[0] == "tag:pin"
    assert "phase:endgame" in keys and "phase:opening" not in keys
    assert "time" in keys and "opening:Sicilian Defense" in keys
    assert weakness.ranked({"summary": {"games": 0, "moves": 0, "errors": 0}}) == []


def test_migrations_add_columns(tmp_path):
    # An M1/M2-era database lacks the columns added later; init_db adds them in place.
    path = tmp_path / "old.db"
    db.init_db(path)
    con = db.connect(path)
    con.execute("ALTER TABLE games DROP COLUMN reviewed_at")
    con.execute("ALTER TABLE analysis_runs DROP COLUMN tagged")
    con.close()
    db.init_db(path)
    con = db.connect(path)
    assert "reviewed_at" in {r["name"] for r in con.execute("PRAGMA table_info(games)")}
    assert "tagged" in {r["name"] for r in con.execute("PRAGMA table_info(analysis_runs)")}
    con.close()
