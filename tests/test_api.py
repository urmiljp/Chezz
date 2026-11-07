import sqlite3

from chezz import ingest
from conftest import SAMPLE


def test_import_list_and_filters(client):
    res = client.post("/api/import", json={"pgn": SAMPLE}).json()
    assert (res["added"], res["duplicates"]) == (14, 2)

    meta = client.get("/api/meta").json()
    assert meta["total"] == 14 and meta["user_games"] == 14
    assert set(meta["time_classes"]) <= {"bullet", "blitz", "rapid", "classical"}

    all_games = client.get("/api/games").json()
    assert all_games["total"] == 14
    dates = [g["date"] for g in all_games["games"]]
    assert dates == sorted(dates, reverse=True)

    white = client.get("/api/games", params={"color": "white"}).json()
    assert white["total"] == 7 and all(g["user_color"] == "white" for g in white["games"])

    wins = client.get("/api/games", params={"result": "win"}).json()
    assert all(g["user_result"] == "win" for g in wins["games"])

    rng = client.get("/api/games", params={"date_from": "2026-09-01", "date_to": "2026-09-10"}).json()
    assert rng["total"] > 0 and all("2026-09-01" <= g["date"] <= "2026-09-10" for g in rng["games"])

    fam = meta["openings"][0]["name"]
    assert all(g["opening_family"] == fam for g in client.get("/api/games", params={"opening": fam}).json()["games"])

    page = client.get("/api/games", params={"limit": 5, "offset": 10}).json()
    assert len(page["games"]) == 4 and page["total"] == 14


def test_star_tags_notes_and_delete(client):
    client.post("/api/import-sample")
    gid = client.get("/api/games").json()["games"][0]["id"]

    out = client.patch(f"/api/games/{gid}", json={"starred": True, "tags": [" endgame ", "blunder", "blunder"], "notes": "Missed Nf7"}).json()
    assert out["starred"] is True and out["tags"] == ["blunder", "endgame"]

    assert client.get("/api/games", params={"starred": True}).json()["total"] == 1
    assert client.get("/api/games", params={"tag": "endgame"}).json()["total"] == 1
    assert client.get("/api/games", params={"q": "Missed"}).json()["total"] == 1
    assert client.get("/api/meta").json()["tags"] == [{"name": "blunder", "count": 1}, {"name": "endgame", "count": 1}]

    detail = client.get(f"/api/games/{gid}").json()
    assert detail["notes"] == "Missed Nf7" and len(detail["moves"]) == detail["ply_count"]
    assert detail["moves"][0]["clock"] is not None

    assert client.delete(f"/api/games/{gid}").json() == {"ok": True}
    assert client.get(f"/api/games/{gid}").status_code == 404
    assert client.get("/api/meta").json()["tags"] == []  # tag rows cascade


def test_backup_and_export(client, cfg):
    client.post("/api/import-sample")
    path = client.post("/api/backup").json()["path"]
    con = sqlite3.connect(path)
    assert con.execute("SELECT count(*) FROM games").fetchone()[0] == 14
    con.close()

    exp = client.post("/api/export").json()
    assert exp["count"] == 14
    text = open(exp["path"], encoding="utf-8").read()
    assert "[%clk" in text
    assert len(list(ingest.iter_games(text))) == 14
    # Round trip: exported PGN re-imports as pure duplicates.
    assert client.post("/api/import", json={"pgn": text}).json() == {"added": 0, "duplicates": 14, "errors": [], "ids": []}


def test_wal_mode_enabled(cfg, client):
    con = sqlite3.connect(cfg.db_path)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<div id=\"app\">" in r.text


def test_list_query_uses_indexes(con):
    plan = " ".join(r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM games g WHERE g.user_color = 'white' AND g.user_result = 'win'"
    ))
    assert "INDEX games_user" in plan
    plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN SELECT game_id FROM game_tags WHERE tag = 'x'"))
    assert "game_tags_tag" in plan


def test_exit_calls_launcher_hook(client):
    import time

    assert client.post("/api/exit").status_code == 400  # no launcher hook (e.g. tests)
    called = []
    client.app.state.request_exit = lambda: called.append(True)
    assert client.post("/api/exit").json() == {"ok": True}
    time.sleep(0.6)
    assert called == [True]
