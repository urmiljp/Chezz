"""Openings, lessons content, endgames, today's plan, streak and play."""
import io
from datetime import date, timedelta

import chess
import chess.pgn
import pytest

from chezz import lessons, openings, plan, play, puzzles
from chezz.analysis import find_engine
from chezz.config import Config

ENGINE = find_engine(Config())


def test_eco_book_and_classification(con):
    assert openings.load_eco(con) is True
    assert openings.load_eco(con) is False  # versioned: no reload
    assert con.execute("SELECT count(*) FROM eco").fetchone()[0] > 3000
    game = chess.pgn.read_game(io.StringIO("1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 *"))
    eco, name = openings.classify(con, game)
    assert eco.startswith("C5") and name.startswith("Italian Game")
    assert openings.family(name) == "Italian Game"


def test_import_uses_book_names(client):
    client.post("/api/import-sample")
    families = {o["name"] for o in client.get("/api/meta").json()["openings"]}
    # chess.com URL slugs and lichess headers now share the book's spelling.
    book_families = {openings.family(row[2]) for row in openings._eco_data()[0]}
    assert families and families <= book_families
    assert "Queens Pawn Opening London System" not in families


def test_repertoire_build_drill_and_deviations(client):
    client.post("/api/import-sample")
    added = client.post("/api/openings/build", json={"color": "white"}).json()["added"]
    assert added > 0
    assert client.post("/api/openings/build", json={"color": "white"}).json()["added"] == 0  # idempotent
    s = client.get("/api/openings/summary").json()
    assert s["white"]["cards"] == added and s["white"]["due"] == added

    card = client.get("/api/openings/drill", params={"color": "white"}).json()
    board = chess.Board()
    for u in card["line"]:
        board.push_uci(u)
    assert board.fen() == card["fen"] and board.turn == chess.WHITE
    graded = client.post("/api/openings/drill", json={"color": "white", "epd": card["epd"], "correct": True}).json()
    assert graded == {"box": 1, "due_in_days": 1}
    assert client.get("/api/openings/summary").json()["white"]["due"] == added - 1

    pos = client.get("/api/openings/explore", params={"color": "white", "fen": chess.STARTING_FEN}).json()
    assert pos["your_turn"] and pos["repertoire_move"] == "e2e4"
    assert any(c["san"] == "d4" and c["book"] for c in pos["candidates"])
    # Switch the first move to d4 manually.
    changed = client.post("/api/openings/repertoire", json={"color": "white", "fen": chess.STARTING_FEN, "line": [], "uci": "d2d4"}).json()
    assert changed["repertoire_move"] == "d2d4" and changed["manual"]

    devs = client.get("/api/openings/deviations").json()
    assert len(devs) == 14
    assert all(d["left_book"] is None or d["left_book"]["ply"] >= 1 for d in devs)
    # Games starting with 1.e4 now deviate from the (d4) repertoire on move 1.
    assert any(d["left_repertoire"] and d["left_repertoire"]["ply"] == 1 for d in devs)
    g = client.get(f"/api/games/{devs[0]['game_id']}").json()
    assert "left_book" in g["opening_deviations"]

    # New cards are due today in local time (not UTC), so a fresh repertoire is drillable straight away.
    import sqlite3
    con = sqlite3.connect(client.app.state.worker.cfg.db_path)
    assert con.execute("SELECT count(*) FROM repertoire WHERE manual = 0 AND due != date('now', 'localtime')").fetchone()[0] == 0
    con.close()


def test_endgame_practice_games_are_not_opening_deviations(client):
    """Games from a set position (endgame practice) have no opening; they must not show as leaving book on move 1."""
    fen = "8/8/8/4k3/8/8/4P3/4K3 w - - 0 1"
    gid = client.post("/api/play/finish", json={"start_fen": fen, "moves": ["e1d2"], "user_color": "white",
                                                 "result": "1/2-1/2", "elo": 1500}).json()["game_id"]
    assert gid
    assert all(d["game_id"] != gid for d in client.get("/api/openings/deviations").json())
    assert client.get(f"/api/games/{gid}").json()["opening_deviations"] == {"left_book": None, "left_repertoire": None}


def test_opening_lessons_lines():
    ops = [l for l in lessons.load_all() if "side" in l]
    assert len(ops) == 20 and len({l["id"] for l in ops}) == 20
    assert {l["category"] for l in ops} == {"Openings for White", "Openings for Black"}
    for l in ops:
        assert l["body"] and 4 <= len(l["examples"]) <= 7
        assert l["drill"] == {"themes": ["opening"], "count": len(l["examples"]), "mode": "lines"}
        assert all(w.startswith("opening:") for w in l["weakness"])
        for ex in l["examples"]:
            assert ex["orientation"] == l["side"] and (ex["setup"] is not None) == (l["side"] == "black")
            moves = ([ex["setup"]] if ex["setup"] else []) + ex["solution"]
            assert len(ex["san"]) == len(ex["notes"]) == len(moves) >= 6
            assert len(ex["solution"]) % 2 == 1, (l["id"], ex["prompt"])  # ends on your move
            own = 1 if ex["setup"] else 0
            assert ex["explain"] and all(ex["notes"][own::2]), (l["id"], ex["prompt"])  # every move of yours explained
            board = chess.Board()
            for uci, san in zip(moves, ex["san"]):
                assert board.san(chess.Move.from_uci(uci)) == san
                board.push_uci(uci)


def test_opening_line_errors_are_reported():
    with pytest.raises(ValueError, match="must end on a white move"):
        lessons._expand_line({"name": "x", "pgn": "1. e4 e5"}, "white")
    with pytest.raises(ValueError, match="Bad line"):
        lessons._expand_line({"name": "x", "pgn": "1. e4 e5 2. Ke3 Ke6 3. Qh5"}, "white")


def test_lesson_lines_to_repertoire(client):
    out = client.post("/api/lessons/italian-game/repertoire").json()
    # 5 lines share 1.e4, 2.Nf3, 3.Bc4; 4.c3 vs 4.b4 (Evans) and 5.d4 vs 5.d3 clash with the first line.
    assert out["color"] == "white" and out["added"] > 20 and out["kept"] == 2
    again = client.post("/api/lessons/italian-game/repertoire").json()
    assert again["added"] == 0 and again["kept"] == 2
    card = client.get("/api/openings/drill", params={"color": "white"}).json()
    assert card["move_uci"] == "e2e4" and card["line"] == []
    black = client.post("/api/lessons/sicilian-defense/repertoire").json()
    assert black["color"] == "black" and black["added"] > 20
    assert client.post("/api/lessons/forks/repertoire").status_code == 404


def test_lessons_content_is_valid():
    all_lessons = lessons.load_all()
    assert len(all_lessons) == 32
    cats = {l["category"] for l in all_lessons if "side" not in l}
    assert cats == {"Tactics", "Checkmates", "Opening principles", "Endgames"}
    for l in all_lessons:
        if "side" in l:
            continue  # opening lessons: test_opening_lessons_lines
        assert l["body"] and 2 <= len(l["examples"]) <= 3
        assert l["drill"]["count"] == 10 and set(l["drill"]["themes"]) <= set(puzzles.THEMES)
        for ex in l["examples"]:
            board = chess.Board(ex["fen"])
            assert board.is_valid()
            assert len(ex["solution"]) % 2 == 1  # ends on the solver's move
            assert len(ex["accept"]) == (len(ex["solution"]) + 1) // 2
            for i, uci in enumerate(ex["solution"]):
                move = chess.Move.from_uci(uci)
                assert move in board.legal_moves, (l["id"], uci)
                if i % 2 == 0:
                    assert uci in ex["accept"][i // 2]
                board.push(move)


def test_lesson_listing_progress_and_recommendation(client, con):
    weak = [{"key": "tag:fork", "label": "Fork", "detail": "3 allowed"}]
    items = lessons.listing(con, weak)
    rec = [l for l in items if l["recommended"]]
    assert rec[0]["id"] == "forks" and rec[0]["reason"].startswith("Fork")
    # With no data, the first lesson is recommended.
    assert [l["id"] for l in lessons.listing(con, []) if l["recommended"]] == ["hanging-pieces"]

    out = client.post("/api/lessons/forks/progress", json={"examples_done": 2, "drill_correct": 8, "drill_total": 10, "completed": True}).json()
    assert out["completed_at"] and out["drill_correct"] == 8
    listing = client.get("/api/lessons").json()
    assert next(l for l in listing if l["id"] == "forks")["completed"]
    assert client.get("/api/lessons/forks").json()["examples"][0]["solution"]
    assert client.get("/api/lessons/nope").status_code == 404


def test_endgames_content():
    for e in play.endgames():
        board = chess.Board(e["fen"])
        assert board.is_valid() and not board.is_game_over()
        assert e["goal"] in ("promote", "mate", "hold")
        assert ("white" if board.turn == chess.WHITE else "black") == e["side"]
        assert e["goal"] != "hold" or e["moves"] > 0


def test_streak(con):
    today = date.today()
    for d in (0, 1, 2, 5, 6, 7, 8):
        con.execute("INSERT INTO activity (day, kind, count) VALUES (?, 'puzzle', 1)", ((today - timedelta(days=d)).isoformat(),))
    con.commit()
    s = plan.streak(con)
    assert s["current"] == 3 and s["best"] == 4 and s["today"]
    assert len(s["days"]) == 84 and s["days"][-1]["day"] == today.isoformat()
    con.execute("DELETE FROM activity WHERE day = ?", (today.isoformat(),))
    s = plan.streak(con)
    assert s["current"] == 2 and not s["today"]  # yesterday's streak still counts until today ends


def test_plan_and_progress_endpoints(client):
    empty = client.get("/api/plan").json()
    assert empty["games"] == 0 and empty["game"] is None and empty["puzzles"]["themes"] == plan.DEFAULT_THEMES
    assert empty["lesson"]["id"] == "hanging-pieces"
    client.post("/api/import-sample")
    p = client.get("/api/plan").json()
    assert p["game"]["analysed"] is False  # nothing analysed yet: suggest the latest game
    assert p["puzzles"]["target"] == 5 and p["streak"]["current"] == 0
    gid = p["game"]["id"]
    client.post(f"/api/games/{gid}/reviewed")
    assert client.get("/api/plan").json()["streak"]["current"] == 1
    prog = client.get("/api/progress").json()
    assert prog["totals"]["games"] == 14 and prog["games"] == [] and prog["streak"]["today"]


def test_strength_options():
    opts, limit = play.strength_options(1500)
    assert opts["UCI_LimitStrength"] and opts["UCI_Elo"] == 1500
    weak, wl = play.strength_options(600)
    assert not weak["UCI_LimitStrength"] and weak["Skill Level"] == 0 and wl.depth == 1
    mid, ml = play.strength_options(1200)
    assert 0 < mid["Skill Level"] <= 5 and ml.depth > 1
    assert play.strength_options(3200)[0]["Skill Level"] == 20


def test_finish_game_saves_and_endgame_progress(client):
    moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
    out = client.post("/api/play/finish", json={"moves": moves, "user_color": "white", "result": "0-1", "elo": 1500,
                                                "termination": "Checkmate"}).json()
    g = client.get(f"/api/games/{out['game_id']}").json()
    assert g["tags"] == ["vs-engine"] and g["black"] == "Stockfish (1500)" and g["user_result"] == "loss"
    assert len(g["moves"]) == 4
    # Endgame start positions are stored with their FEN.
    e = play.endgames()[0]
    out2 = client.post("/api/play/finish", json={"start_fen": e["fen"], "moves": ["e5d6", "e8d8"], "user_color": "white",
                                                 "result": "1/2-1/2", "elo": 3000}).json()
    assert client.get(f"/api/games/{out2['game_id']}").json()["start_fen"] == e["fen"]
    assert client.post("/api/play/finish", json={"moves": ["e2e5"], "user_color": "white", "result": "1-0", "elo": 1500}).status_code == 400
    r = client.post(f"/api/endgames/{e['id']}/result", json={"success": True}).json()
    assert r["attempts"] == 1 and r["successes"] == 1
    assert next(x for x in client.get("/api/endgames").json() if x["id"] == e["id"])["successes"] == 1


@pytest.mark.skipif(ENGINE is None, reason="Stockfish not installed")
def test_engine_plays_legal_moves_at_all_strengths(tmp_path):
    eng = play.PlayEngine(ENGINE)
    try:
        board = chess.Board()
        for elo in (600, 1100, 1500, 2200, 3200):
            move = eng.move(board.fen(), elo)
            assert move in board.legal_moves
            board.push(move)
        mate = eng.move("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", 3200)
        assert mate.uci() == "a1a8"
        with pytest.raises(ValueError):  # black is already mated
            eng.move("R5k1/5ppp/8/8/8/8/8/6K1 b - - 0 1", 1500)
    finally:
        eng.close()
