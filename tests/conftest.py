import pytest
from fastapi.testclient import TestClient

from chezz import db
from chezz.api import create_app
from chezz.config import ROOT, Config

SAMPLE = (ROOT / "samples" / "games.pgn").read_text(encoding="utf-8")


@pytest.fixture
def cfg(tmp_path):
    return Config(usernames=["SamplePlayer"], data_dir=tmp_path / "data")


@pytest.fixture
def con(cfg):
    db.init_db(cfg.db_path)
    c = db.connect(cfg.db_path)
    yield c
    c.close()


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg), base_url="http://127.0.0.1") as c:
        yield c
