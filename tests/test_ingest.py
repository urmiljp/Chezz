import io

import chess.pgn
import pytest

from chezz import ingest
from conftest import SAMPLE

CHESSCOM = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.10.02"]
[White "SamplePlayer"]
[Black "opp"]
[Result "1-0"]
[TimeControl "600"]
[ECO "C50"]
[ECOUrl "https://www.chess.com/openings/Italian-Game-Two-Knights-Defense-4.d3"]
[Link "https://www.chess.com/game/live/42"]

1. e4 {[%clk 0:09:58]} 1... e5 {[%clk 0:09:57]} 2. Nf3 {[%clk 0:09:50]} 2... Nc6 {[%clk 0:09:40]}
3. Bc4 {[%clk 0:09:45]} 3... Nf6 {[%clk 0:09:30]} 4. Ng5 {[%clk 0:09:40]} 4... d5 {[%clk 0:09:10]} 1-0
"""

OTB = """[Event "Club"]
[Site "Riverside"]
[Date "2026.10.03"]
[White "a"]
[Black "SamplePlayer"]
[Result "0-1"]

1. f3 e5 2. g4 Qh4# 0-1
"""


def test_sample_file_imports_with_built_in_duplicates(con):
    res = ingest.import_pgn(con, SAMPLE, ["SamplePlayer"])
    assert res.errors == []
    assert (res.added, res.duplicates) == (14, 2)
    assert con.execute("SELECT count(*) FROM games").fetchone()[0] == 14


def test_reimport_is_fully_deduped(con):
    ingest.import_pgn(con, SAMPLE, ["SamplePlayer"])
    res = ingest.import_pgn(con, SAMPLE, ["SamplePlayer"])
    assert (res.added, res.duplicates) == (0, 16)


def test_dedupe_by_link_header_even_if_moves_differ(con):
    ingest.import_pgn(con, CHESSCOM, [])
    altered = CHESSCOM.replace("4... d5", "4... Bc5")
    res = ingest.import_pgn(con, altered, [])
    assert res.duplicates == 1 and res.added == 0


def test_dedupe_by_movetext_sha1_ignores_formatting(con):
    ingest.import_pgn(con, OTB, [])
    reformatted = OTB.replace("1. f3 e5 2. g4 Qh4# 0-1", "1.f3   e5\r\n2.g4\r\nQh4#\r\n0-1").replace("\n", "\r\n")
    res = ingest.import_pgn(con, reformatted, [])
    assert res.duplicates == 1 and res.added == 0


def test_sha1_distinguishes_different_games(con):
    ingest.import_pgn(con, OTB, [])
    other = OTB.replace("1. f3 e5 2. g4 Qh4# 0-1", "1. f4 e5 2. g4 Qh4# 0-1")
    assert ingest.import_pgn(con, other, []).added == 1


def test_sha1_key_ignores_headers_but_link_key_uses_link():
    g1 = chess.pgn.read_game(io.StringIO(OTB))
    g2 = chess.pgn.read_game(io.StringIO(OTB.replace('[Event "Club"]', '[Event "Other"]')))
    assert ingest.dedupe_key(g1) == ingest.dedupe_key(g2)
    assert ingest.dedupe_key(g1).startswith("sha1:")
    g3 = chess.pgn.read_game(io.StringIO(CHESSCOM))
    assert ingest.dedupe_key(g3) == "link:https://www.chess.com/game/live/42"


def test_lichess_site_url_is_used_as_link(con):
    pgn = OTB.replace('[Site "Riverside"]', '[Site "https://lichess.org/abcd1234"]')
    ingest.import_pgn(con, pgn, [])
    row = con.execute("SELECT dedupe_key, link FROM games").fetchone()
    assert row["dedupe_key"] == "link:https://lichess.org/abcd1234"
    assert row["link"] == "https://lichess.org/abcd1234"


def test_metadata_and_clock_comments_are_kept(con):
    res = ingest.import_pgn(con, CHESSCOM, ["sampleplayer"])  # username match is case-insensitive
    row = con.execute("SELECT * FROM games WHERE id = ?", (res.ids[0],)).fetchone()
    assert row["date"] == "2026-10-02"
    assert (row["user_color"], row["user_result"]) == ("white", "win")
    assert row["time_class"] == "rapid"
    assert row["opening"] == "Italian Game Two Knights Defense"
    assert row["opening_family"] == "Italian Game"
    assert row["ply_count"] == 8
    assert "[%clk 0:09:58]" in row["pgn"]
    moves = ingest.game_moves(row["pgn"])["moves"]
    assert moves[0]["clock"] == 598 and moves[0]["san"] == "e4"


def test_bad_games_are_reported_not_imported(con):
    bad = '[White "x"]\n[Black "y"]\n\n1. e4 e5 2. Ke3 *\n\n[Variant "Chess960"]\n[White "p"]\n\n1. e4 *\n\n[White "q"]\n\n*\n'
    res = ingest.import_pgn(con, bad + "\n" + OTB, [])
    assert res.added == 1
    assert len(res.errors) == 3


@pytest.mark.parametrize(
    "tc, expected",
    [("60", "bullet"), ("120+1", "bullet"), ("180", "blitz"), ("180+2", "blitz"), ("300", "blitz"),
     ("600", "rapid"), ("900+10", "rapid"), ("1800", "classical"), ("1/86400", "daily"), ("-", None), (None, None)],
)
def test_time_class(tc, expected):
    assert ingest.time_class(tc) == expected


@pytest.mark.parametrize(
    "value, expected",
    [("2024.03.15", "2024-03-15"), ("2024.??.??", "2024"), ("????.??.??", None), (None, None)],
)
def test_normalize_date(value, expected):
    assert ingest.normalize_date(value) == expected


@pytest.mark.parametrize(
    "white, black, result, expected",
    [("Me", "x", "1-0", ("white", "win")), ("x", "me", "1-0", ("black", "loss")),
     ("x", "ME", "1/2-1/2", ("black", "draw")), ("x", "y", "1-0", (None, None)), ("me", "x", "*", ("white", None))],
)
def test_user_fields(white, black, result, expected):
    assert ingest.user_fields(white, black, result, ["me"]) == expected


def test_opening_family():
    assert ingest.opening_family("Sicilian Defense: Najdorf Variation") == "Sicilian Defense"
    assert ingest.opening_family("Queen's Gambit Declined") == "Queen's Gambit"
    assert ingest.opening_family("Ruy Lopez: Morphy Defense") == "Ruy Lopez"


def test_refresh_user_fields_after_username_change(con):
    ingest.import_pgn(con, OTB, [])
    assert con.execute("SELECT user_color FROM games").fetchone()[0] is None
    ingest.refresh_user_fields(con, ["SamplePlayer"])
    assert tuple(con.execute("SELECT user_color, user_result FROM games").fetchone()) == ("black", "win")
