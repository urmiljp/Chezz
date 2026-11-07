import time

import pytest
import zstandard
from fastapi.testclient import TestClient

from chezz import puzzles
from chezz.analysis import find_engine
from chezz.api import create_app
from chezz.config import Config

ENGINE = find_engine(Config())

CSV = """PuzzleId,FEN,Moves,Rating,RatingDeviation,Popularity,NbPlays,Themes,GameUrl,OpeningTags
p1,r3k3/8/8/1N6/8/8/5PPP/6K1 b - - 0 1,a8a7 b5c7,1200,80,95,1000,fork short,https://lichess.org/x,
p2,4k3/8/8/3n4/8/8/8/3QK3 b - - 0 1,e8e7 d1d5,1450,80,95,1000,hangingPiece short,https://lichess.org/y,
p3,6k1/5ppp/8/8/8/8/8/R5K1 b - - 0 1,g8h8 a1a8,1800,80,95,1000,mateIn1 backRankMate,https://lichess.org/z,
p4,6k1/5ppp/8/8/8/8/8/R5K1 b - - 0 1,g8h8 a1a8,1300,80,10,1000,mateIn1,https://lichess.org/w,
"""


def test_glicko_moves_toward_result():
    r, rd = puzzles.glicko(1500, 350, 1500, 75, 1.0)
    assert r > 1500 and rd < 350
    r2, _ = puzzles.glicko(1500, 350, 1500, 75, 0.0)
    assert r2 < 1500 and abs((r - 1500) - (1500 - r2)) < 1e-6  # symmetric at equal ratings
    # Beating a much stronger puzzle gains more than beating a weaker one.
    assert puzzles.glicko(1500, 100, 1900, 75, 1.0)[0] - 1500 > puzzles.glicko(1500, 100, 1100, 75, 1.0)[0] - 1500
    # RD never drops below the floor, so the rating keeps adapting.
    rd = 350
    for _ in range(200):
        _, rd = puzzles.glicko(1500, rd, 1500, 75, 1.0)
    assert rd == puzzles.MIN_RD


def test_leitner_schedule():
    assert puzzles.leitner(None, True) == (None, None)    # solved, never failed: no review
    assert puzzles.leitner(None, False) == (1, 1)         # failed: box 1, tomorrow
    assert puzzles.leitner(1, True) == (2, 3)
    assert puzzles.leitner(4, True) == (5, 30)
    assert puzzles.leitner(5, True) == (None, None)       # retired
    assert puzzles.leitner(4, False) == (1, 1)


@pytest.fixture
def lichess_file(tmp_path):
    path = tmp_path / "lichess_db_puzzle.csv.zst"
    path.write_bytes(zstandard.ZstdCompressor().compress(CSV.encode()))
    return path


def test_lichess_import_and_selection(con, cfg, lichess_file):
    db_path = cfg.data_dir / "lichess_puzzles.db"
    n = puzzles.import_lichess_file(lichess_file, db_path, min_popularity=80, min_plays=300)
    assert n == 3  # p4 filtered out by popularity
    assert puzzles.lichess_count(db_path) == 3

    p = puzzles.next_puzzle(con, db_path, "lichess", theme="fork")
    assert p["moves"] == ["a8a7", "b5c7"] and "fork" in p["themes"]
    assert puzzles.next_puzzle(con, db_path, "lichess", theme="mateIn1")["rating"] == 1800

    # Solving rates the first attempt only; failing schedules a review for tomorrow.
    res = puzzles.record_attempt(con, p["id"], solved=False, hints=0, seconds=12)
    assert res["rated"] and res["delta"] < 0 and res["box"] == 1 and res["due_in_days"] == 1
    again = puzzles.record_attempt(con, p["id"], solved=True, hints=0, seconds=5)
    assert not again["rated"] and again["delta"] == 0
    assert puzzles.summary(con, db_path)["in_review"] == 1
    con.execute("UPDATE puzzle_reviews SET due = date('now', 'localtime')")
    con.commit()
    assert puzzles.next_puzzle(con, db_path, "review")["id"] == p["id"]
    assert puzzles.next_puzzle(con, db_path, "review", exclude=[p["id"]]) is None

    # Attempted puzzles are not served again.
    seen = {p["id"]}
    for _ in range(2):
        q = puzzles.next_puzzle(con, db_path, "lichess")
        assert q["id"] not in seen
        puzzles.record_attempt(con, q["id"], True, 0, 3)
        seen.add(q["id"])
    assert puzzles.next_puzzle(con, db_path, "lichess") is None


def test_importer_thread_and_api(client, lichess_file):
    r = client.post("/api/puzzles/lichess/import", json={"path": str(lichess_file)})
    assert r.status_code == 200
    client.app.state.lichess_importer.join(10)
    status = client.get("/api/puzzles/lichess").json()
    assert status["count"] == 3 and status["import"]["error"] is None
    p = client.get("/api/puzzles/next", params={"mode": "mixed"}).json()
    out = client.post(f"/api/puzzles/{p['id']}/attempt", json={"solved": True, "hints": 0, "seconds": 4}).json()
    assert out["rated"] and out["rating"] > 1500
    assert client.get("/api/puzzles/summary").json()["solved"] == 1
    assert client.post("/api/puzzles/lichess/import", json={"path": "nope.zst"}).status_code == 400
    r = client.get("/api/puzzles/next", params={"mode": "own"})
    assert r.status_code == 200 and r.json() is None  # nothing available is an empty answer, not an error


# Black (the user) misses 2...Bxg4 winning the queen.
MISSED_QUEEN = """[Event "t"]
[White "opp"]
[Black "SamplePlayer"]
[Result "*"]

1. e4 d5 2. Qg4 a6 3. Qd1 *
"""


@pytest.mark.skipif(ENGINE is None, reason="Stockfish not installed")
def test_own_blunder_puzzle_generated(tmp_path):
    cfg = Config(usernames=["SamplePlayer"], data_dir=tmp_path, engine_path=ENGINE, depth=10, threads=1, hash_mb=16)
    with TestClient(create_app(cfg), base_url="http://127.0.0.1") as c:
        gid = c.post("/api/import", json={"pgn": MISSED_QUEEN}).json()["ids"][0]
        c.post(f"/api/games/{gid}/analyze")
        deadline = time.time() + 60
        while time.time() < deadline:
            s = c.get("/api/puzzles/summary").json()
            if s["own_total"]:
                break
            time.sleep(0.2)
        p = c.get("/api/puzzles/next", params={"mode": "own"}).json()
        assert p["source"] == "own" and p["game_id"] == gid and p["ply"] == 4
        assert p["moves"][:2] == ["d1g4", "c8g4"]  # setup move, then the missed capture
        assert "hangingPiece" in p["themes"]
        review = c.get(f"/api/games/{gid}/review").json()
        tags = review["moves"][3]["tags"]
        assert {"tag": "hanging_piece", "kind": "missed", "label": "Hanging piece"} in tags


def _own_puzzle(con, n):
    con.execute(
        "INSERT INTO puzzles (source, source_id, fen, moves, rating, themes) VALUES ('own', ?, ?, 'a8a7 b5c7', 1200, 'fork')",
        (f"g:{n}", "r3k3/8/8/1N6/8/8/5PPP/6K1 b - - 0 1"),
    )
    con.commit()
    return con.execute("SELECT max(id) FROM puzzles").fetchone()[0]


def test_skip_moves_on_in_own_and_review_modes(con, cfg):
    """Regression: skipping in 'My mistakes' returned the same puzzle; excluded ids must be honoured."""
    path = cfg.data_dir / "none.db"
    a, b = _own_puzzle(con, 1), _own_puzzle(con, 2)
    first = puzzles.next_puzzle(con, path, "own")["id"]
    second = puzzles.next_puzzle(con, path, "own", exclude=[first])["id"]
    assert {first, second} == {a, b}
    assert puzzles.next_puzzle(con, path, "own", exclude=[a, b]) is None

    # A failed puzzle is due tomorrow; once due, the Review count and the Review picker agree.
    puzzles.record_attempt(con, a, solved=False, hints=0, seconds=3)
    con.execute("UPDATE puzzle_reviews SET due = date('now', 'localtime')")
    assert puzzles.summary(con, path)["due"] == 1
    assert puzzles.next_puzzle(con, path, "review")["id"] == a
