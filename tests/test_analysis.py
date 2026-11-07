"""Worker + API flow against a real Stockfish (skipped if none is installed)."""
import time

import pytest
from fastapi.testclient import TestClient

from chezz.analysis import find_engine
from chezz.api import create_app
from chezz.config import Config

# Scholar's mate: 2...Nf6?? allows mate; 3...Nf6?? is the blunder, 4.Qxf7#.
MATE_PGN = """[Event "t"]
[White "opp"]
[Black "SamplePlayer"]
[Result "1-0"]
[TimeControl "60"]

1. e4 {[%clk 0:00:59]} 1... e5 {[%clk 0:00:59]} 2. Bc4 {[%clk 0:00:58]} 2... Nc6 {[%clk 0:00:58]}
3. Qh5 {[%clk 0:00:57]} 3... Nf6 {[%clk 0:00:50]} 4. Qxf7# {[%clk 0:00:56]} 1-0
"""

ENGINE = find_engine(Config())
pytestmark = pytest.mark.skipif(ENGINE is None, reason="Stockfish not installed")


def wait_done(client, gid, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/games/{gid}/review").json()
        if r["pending"] is None and r["run"]:
            return r
        if r["pending"] and r["pending"]["status"] == "error":
            raise AssertionError(r["pending"]["error"])
        time.sleep(0.1)
    raise AssertionError("analysis timed out")


@pytest.fixture
def engine_client(tmp_path):
    cfg = Config(usernames=["SamplePlayer"], data_dir=tmp_path, engine_path=ENGINE, depth=10, threads=1, hash_mb=16)
    with TestClient(create_app(cfg), base_url="http://127.0.0.1") as c:
        yield c


def test_analyse_game_end_to_end(engine_client):
    c = engine_client
    gid = c.post("/api/import", json={"pgn": MATE_PGN}).json()["ids"][0]
    assert c.get("/api/analysis/status").json()["unanalyzed"] == 1

    c.post(f"/api/games/{gid}/analyze")
    r = wait_done(c, gid)

    assert len(r["positions"]) == 8 and len(r["moves"]) == 7
    assert r["positions"][-1]["eval"] == "Mate" and r["positions"][-1]["win"] == 100
    blunder = r["moves"][5]
    assert blunder["san"] == "Nf6" and blunder["classification"] == "blunder"
    assert "mate" in blunder["explanation"].lower()
    assert blunder["refutation_san"] == "Qxf7#"
    assert blunder["time_trouble"] is False
    assert r["moves"][6]["classification"] == "best"
    assert r["run"]["black_accuracy"] < r["run"]["white_accuracy"]
    assert r["run"]["counts"]["black"]["blunder"] >= 1
    assert r["run"]["engine"].lower().startswith("stockfish")

    games = c.get("/api/games").json()["games"]
    assert games[0]["analysis_status"] == "done" and games[0]["accuracy"] == r["run"]["black_accuracy"]

    # Re-run creates a new run; the review reads the newest finished one.
    first = r["run"]["id"]
    c.post(f"/api/games/{gid}/analyze")
    r2 = wait_done(c, gid)
    assert r2["run"]["id"] > first

    # LLM round trip keyed by (fen, move).
    batch = c.post(f"/api/games/{gid}/llm-batch", params={"scope": "key"}).json()
    assert batch["count"] >= 1 and batch["path"].endswith(f"game-{gid}-key-moments.md")
    key_line = next(line for line in batch["text"].splitlines() if line.startswith("### KEY") and "<fen>" not in line)
    reply = f"### SUMMARY {gid}\nBlack fell for a quick mate.\n\n{key_line}\nThis knight move ignores the threat on f7.\n"
    assert c.post("/api/explanations/import", json={"text": reply}).json() == {"imported": 1, "summaries": 1}
    r3 = c.get(f"/api/games/{gid}/review").json()
    m = r3["moves"][5]
    assert m["explanation"] == "This knight move ignores the threat on f7." and m["explanation_source"] == "llm"
    assert r3["coach_summary"] == "Black fell for a quick mate."

    # Whole-game export has one block per move; key moments only the errors.
    whole = c.post(f"/api/games/{gid}/llm-batch", params={"scope": "game"}).json()
    assert whole["count"] == len(r3["moves"]) > batch["count"]
    keys = [line for line in whole["text"].splitlines() if line.startswith("### KEY") and "<fen>" not in line]
    assert len(keys) == whole["count"] and f"### SUMMARY {gid}" in whole["text"]
    assert "Moves: 1. e4" in whole["text"]
    assert c.post(f"/api/games/{gid}/llm-batch", params={"scope": "nope"}).status_code == 400


def test_queue_all_and_engine_reuse(engine_client):
    c = engine_client
    c.post("/api/import-sample")
    worker = c.app.state.worker
    assert c.post("/api/analysis/queue-all").json()["queued"] == 14
    assert c.post("/api/analysis/queue-all").json()["queued"] == 0  # nothing left unqueued
    deadline = time.time() + 300
    engines = set()
    while time.time() < deadline:
        s = c.get("/api/analysis/status").json()
        if worker._engine is not None:
            engines.add(id(worker._engine))
        if s["queued"] == 0 and s["running"] is None:
            break
        time.sleep(0.2)
    assert s["unanalyzed"] == 0
    assert len(engines) == 1  # one engine process reused across all games
