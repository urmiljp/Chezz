"""Build the frontend and zip a ready-to-run copy into dist/chezz-<version>.zip.

The zip contains the built web/dist, so the target machine only needs Python 3.11+ (and Stockfish).
Usage:  .venv/Scripts/python scripts/package.py   (Linux: .venv/bin/python scripts/package.py)
"""
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = [
    "chezz", "assets", "content", "samples", "tests", "scripts", "web/dist", "web/src", "web/index.html",
    "web/package.json", "web/package-lock.json", "web/tsconfig.json", "web/vite.config.ts",
    "pyproject.toml", "config.example.toml", "LICENSE", "run.bat", "run.sh", "README.md", "DEPENDENCIES.md", ".gitattributes",
]


def main() -> None:
    npm = shutil.which("npm")
    if npm:
        subprocess.run([npm, "run", "build"], cwd=ROOT / "web", check=True)
    elif not (ROOT / "web" / "dist" / "index.html").exists():
        sys.exit("web/dist is missing and npm is not installed; build the frontend first.")
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    out = ROOT / "dist" / f"chezz-{version}.zip"
    out.parent.mkdir(exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            path = ROOT / item
            files = [path] if path.is_file() else sorted(f for f in path.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
            for f in files:
                info = zipfile.ZipInfo.from_file(f, arcname=f"chezz/{f.relative_to(ROOT).as_posix()}")
                if f.suffix == ".sh":
                    info.external_attr = 0o100755 << 16  # keep run.sh executable on Linux
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, f.read_bytes())
                count += 1
    print(f"{out} ({count} files, {out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
