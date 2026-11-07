import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    usernames: list[str] = field(default_factory=list)
    engine_path: str = ""
    threads: int = 2
    hash_mb: int = 256
    depth: int = 16
    movetime_ms: int = 0
    port: int = 8765
    data_dir: Path = ROOT / "data"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "chezz.db"


def load_config(path: Path | str | None = None) -> Config:
    path = Path(path or os.environ.get("CHEZZ_CONFIG") or ROOT / "config.toml")
    example = ROOT / "config.example.toml"
    if not path.exists() and path == ROOT / "config.toml" and example.exists():
        shutil.copyfile(example, path)  # first run: give the user a config to edit
    raw = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}  # -sig: tolerate BOM
    engine, app = raw.get("engine", {}), raw.get("app", {})

    names = raw.get("username", [])
    names = [names] if isinstance(names, str) else list(names)
    data_dir = Path(app.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir

    return Config(
        usernames=[n.strip() for n in names if n.strip()],
        engine_path=engine.get("path", ""),
        threads=int(engine.get("threads", 2)),
        hash_mb=int(engine.get("hash", 256)),
        depth=int(engine.get("depth", 16)),
        movetime_ms=int(engine.get("movetime_ms", 0)),
        port=int(app.get("port", 8765)),
        data_dir=data_dir,
    )
