"""Exporting many unexplained games for an AI chat at once, and importing the replies at once.

Analyses are seeded with synthetic engine evals, so these tests need no Stockfish.
"""
import io
import re
from pathlib import Path

import chess
import chess.pgn

from chezz import analysis, db, review

PROMPT_GAME = re.compile(r"^=== Game (\d+): ", re.MULTILINE)


def seed_analysis(con, game_id: int, blunder_ply: int | None = 4, status: str = "done") -> None:
    """Store a finished analysis run with made-up evals. With blunder_ply set, the move reaching that ply
    (Black's 2nd move for ply 4) throws the game away: White goes from 0.00 to +5.00."""
    pgn = con.execute("SELECT pgn FROM games WHERE id = ?", (game_id,)).fetchone()[0]
    game = chess.pgn.read_game(io.StringIO(pgn))
    board = game.board()
    fens, moves = [board.fen()], []
    for node in game.mainline():
        moves.append((node.move.uci(), board.san(node.move)))
        board.push(node.move)
        fens.append(board.fen())
    evals = []
    for ply, fen in enumerate(fens):
        b = chess.Board(fen)
        played = moves[ply][0] if ply < len(moves) else None
        best = played
        if blunder_ply is not None and ply == blunder_ply - 1:  # the engine preferred another move
            best = next(m.uci() for m in b.legal_moves if m.uci() != played)
        cp = 500 if blunder_ply is not None and ply >= blunder_ply else 0
        evals.append({"cp": cp, "mate": None, "win": review.win_percent(cp, None), "best": best, "pv": [best] if best else []})
    run_id = con.execute("INSERT INTO analysis_runs (game_id, depth, status) VALUES (?, 10, ?)", (game_id, status)).lastrowid
    if status == "done":
        rows = analysis.build_rows(fens, moves, evals, [None] * len(moves), None)
        cols = list(rows[0])
        con.executemany(
            f"INSERT INTO move_evals (run_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            [(run_id, *r.values()) for r in rows],
        )
    con.commit()


def ids(client, **params) -> list[int]:
    return [g["id"] for g in client.get("/api/games", params={"limit": 500, **params}).json()["games"]]


def fake_reply(prompt: str, tag: str = "") -> str:
    """What a well-behaved AI chat would answer: a summary per game, then every KEY block."""
    out = ["Sure, here are your games.\n"]
    for line in prompt.splitlines():
        if m := PROMPT_GAME.match(line):
            out.append(f"### SUMMARY {m[1]}\nSummary of game {m[1]}{tag}.\n")
        elif line.startswith("### KEY ") and "<fen>" not in line:
            out.append(f"{line}\nWhy this move matters{tag}.\n")
    return "\n".join(out)


def unexplained(client, **params) -> int:
    return client.get("/api/explanations/unexplained", params=params).json()["count"]


def test_only_analysed_games_without_summary_are_unexplained(client, cfg):
    client.post("/api/import-sample")
    all_ids = ids(client)
    con = db.connect(cfg.db_path)
    for gid in all_ids[:4]:
        seed_analysis(con, gid)
    seed_analysis(con, all_ids[4], status="queued")  # not finished: not ready to explain
    seed_analysis(con, all_ids[5], status="error")
    con.close()
    assert unexplained(client) == 4
    assert client.post("/api/explanations/import", json={"text": f"### SUMMARY {all_ids[0]}\nDone."}).status_code == 200
    assert unexplained(client) == 3
    games = {g["id"]: g for g in client.get("/api/games", params={"limit": 500}).json()["games"]}
    assert games[all_ids[0]]["explained"] is True
    assert not any(games[g]["explained"] for g in all_ids[1:])


def test_batch_export_splits_files_and_respects_filters_and_limit(client, cfg):
    client.post("/api/import-sample")
    all_ids = ids(client)  # newest first
    con = db.connect(cfg.db_path)
    for gid in all_ids:
        seed_analysis(con, gid)
    con.close()
    assert unexplained(client) == 14

    res = client.post("/api/explanations/batch", json={"scope": "key", "per_file": 5}).json()
    assert res["games"] == 14 and [len(p["games"]) for p in res["parts"]] == [5, 5, 4]
    assert [g for p in res["parts"] for g in p["games"]] == all_ids  # newest first, nothing lost or repeated
    for n, part in enumerate(res["parts"], 1):
        path = Path(part["path"])
        assert path.parent == cfg.data_dir / "exports" and path.name.endswith(f"-part-{n}-of-3.md")
        assert path.read_text(encoding="utf-8") == part["text"]
        assert part["text"].startswith(f"# Chezz: explanation batch ({len(part['games'])} games)")
        assert [int(g) for g in PROMPT_GAME.findall(part["text"])] == part["games"]
        keys = [l for l in part["text"].splitlines() if l.startswith("### KEY ") and "<fen>" not in l]
        assert len(keys) == part["count"] == len(part["games"])  # one seeded blunder per game

    # Whole-game scope: one KEY block per move of every game.
    whole = client.post("/api/explanations/batch", json={"scope": "game", "per_file": 50}).json()
    assert len(whole["parts"]) == 1 and "-part-" not in whole["parts"][0]["path"]
    plies = sum(g["ply_count"] for g in client.get("/api/games", params={"limit": 500}).json()["games"])
    assert whole["parts"][0]["count"] == plies

    # Library filters and the "newest N" limit.
    white = client.post("/api/explanations/batch", params={"color": "white"}, json={}).json()
    assert sorted(white["parts"][0]["games"]) == sorted(ids(client, color="white"))
    assert unexplained(client, color="white") == 7
    newest = client.post("/api/explanations/batch", json={"limit": 3}).json()
    assert newest["games"] == 3 and newest["parts"][0]["games"] == all_ids[:3]

    for bad in ({"scope": "nope"}, {"per_file": 0}, {"per_file": 51}, {"limit": 0}):
        assert client.post("/api/explanations/batch", json=bad).status_code == 400


def test_key_scope_includes_error_free_games_for_their_summary(client, cfg):
    client.post("/api/import-sample")
    gid = ids(client)[0]
    con = db.connect(cfg.db_path)
    seed_analysis(con, gid, blunder_ply=None)  # every move matches the engine
    con.close()
    part = client.post("/api/explanations/batch", json={}).json()["parts"][0]
    assert part["games"] == [gid] and part["count"] == 0
    assert part["text"].startswith("# Chezz: explanation batch (1 game)\n") and "The 1 game below is theirs." in part["text"]
    assert analysis.NO_KEY_MOMENTS.strip() in part["text"]
    # Its reply holds just the summary, which is enough to mark it explained.
    assert client.post("/api/explanations/import", json={"text": fake_reply(part["text"])}).json() == {"imported": 0, "summaries": 1}
    assert unexplained(client) == 0


def test_bulk_round_trip_imports_every_reply_at_once(client, cfg):
    client.post("/api/import-sample")
    all_ids = ids(client)
    con = db.connect(cfg.db_path)
    for gid in all_ids:
        seed_analysis(con, gid)
    con.close()

    parts = client.post("/api/explanations/batch", json={"per_file": 4}).json()["parts"]
    assert len(parts) == 4
    # Replies to the first three files pasted together, with chatter and separators between them.
    replies = "\n\n---\n\nAnything else?\n\n".join(fake_reply(p["text"]) for p in parts[:3])
    res = client.post("/api/explanations/import", json={"text": replies}).json()
    done = [g for p in parts[:3] for g in p["games"]]
    assert res == {"imported": sum(p["count"] for p in parts[:3]), "summaries": len(done)}

    for gid in done:
        r = client.get(f"/api/games/{gid}/review").json()
        assert r["coach_summary"] == f"Summary of game {gid}."
        coached = [m for m in r["moves"] if m["explanation_source"] == "llm"]
        assert coached and all(m["explanation"] == "Why this move matters." for m in coached)
        assert {m["classification"] for m in coached} == {"blunder"}

    # Only the games whose reply is still missing are exported next time.
    assert unexplained(client) == 14 - len(done)
    rest = client.post("/api/explanations/batch", json={"per_file": 50}).json()
    assert rest["parts"][0]["games"] == parts[3]["games"]
    client.post("/api/explanations/import", json={"text": fake_reply(rest["parts"][0]["text"])})
    assert unexplained(client) == 0
    err = client.post("/api/explanations/batch", json={})
    assert err.status_code == 400 and "No unexplained games" in err.json()["detail"]

    # A summary for a game that no longer exists is ignored (nothing imported, so it's an error).
    assert client.post("/api/explanations/import", json={"text": "### SUMMARY 99999\nGone."}).status_code == 400


def test_single_game_export_format_unchanged(client, cfg):
    """The per-game prompt keeps its one-game header and layout after the multi-game refactor."""
    client.post("/api/import-sample")
    gid = ids(client)[0]
    con = db.connect(cfg.db_path)
    seed_analysis(con, gid)
    con.close()
    text = client.post(f"/api/games/{gid}/llm-batch", params={"scope": "key"}).json()["text"]
    assert text.startswith("# Chezz: explanation batch\n\nYou are a friendly chess coach")
    assert f"### SUMMARY {gid}\n<your summary>" in text and "=== Game" not in text
    assert "\nGame: " in text and "\nOpening: " in text and "\nMoves: 1. " in text
