"""The local API must refuse requests that other websites can make from the user's browser."""
import chess
import pytest

from chezz import play

EVIL = {"Origin": "https://evil.example"}


def test_same_origin_requests_work(client):
    assert client.get("/api/meta").status_code == 200
    assert client.post("/api/backup", headers={"Origin": "http://127.0.0.1"}).status_code == 200
    assert client.get("/api/meta", headers={"Host": "localhost:8765"}).status_code == 200


def test_dns_rebinding_host_is_refused(client):
    assert client.get("/api/meta", headers={"Host": "attacker.example:8765"}).status_code == 403
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 403


@pytest.mark.parametrize("path", ["/api/exit", "/api/backup", "/api/analysis/queue-all", "/api/pick-file"])
def test_cross_site_posts_are_refused(client, path):
    assert client.post(path, headers=EVIL).status_code == 403
    assert client.post(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_origin_must_match_host_port(client):
    assert client.post("/api/backup", headers={"Origin": "http://127.0.0.1:9999"}).status_code == 403


@pytest.mark.parametrize("path", [r"\\attacker\share\lichess_db_puzzle.csv", "//attacker/share/x.csv", "C:/Windows/win.ini"])
def test_lichess_import_only_takes_local_puzzle_files(client, path):
    assert client.post("/api/puzzles/lichess/import", json={"path": path}).status_code == 400


def test_impossible_position_never_reaches_the_engine():
    eng = play.PlayEngine("not-used")  # raises before the engine would be started
    with pytest.raises(ValueError, match="Illegal position"):
        eng.move("k7/8/8/8/8/8/8/R6K w - - 0 1", 1500)  # black in check with white to move
    assert chess.Board("k7/8/8/8/8/8/8/R6K w - - 0 1").is_valid() is False
