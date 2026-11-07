# Dependencies

Everything Chezz needs, what each piece is for, and its license. `run.bat` / `run.sh` install the
Python and JavaScript packages automatically. You only install the **system requirements** yourself
(see the [README](README.md#installation)).

"Tested with" is the version used during development. The minimum versions are set in
[`pyproject.toml`](pyproject.toml) and [`web/package.json`](web/package.json).

## System requirements

| Software | Version | Purpose | License |
|---|---|---|---|
| [Python](https://www.python.org/) | 3.11+ (3.12 recommended) | Runs the app | PSF |
| [Node.js](https://nodejs.org/) + npm | 18+ | Builds the interface on first start (not needed with a release zip) | MIT |
| [Stockfish](https://stockfishchess.org/) | 16+ | Chess engine for reviews, puzzles and play. Installed separately, not bundled | GPL-3.0 |
| Git | any | Downloading and updating the code (optional) | GPL-2.0 |

**Desktop window** (optional; without it Chezz opens in your browser):

| Platform | Needs | Notes |
|---|---|---|
| Windows 10/11 | Microsoft Edge WebView2 runtime | Already installed on current Windows |
| Linux | PyGObject + WebKit2GTK 4.1 | Debian/Ubuntu: `python3-gi gir1.2-webkit2-4.1` · Fedora: `python3-gobject webkit2gtk4.1` · Arch: `python-gobject webkit2gtk-4.1` |
| macOS | nothing extra | Uses the built-in WebKit through pyobjc (installed automatically) |

## Python packages

### Runtime (from `pyproject.toml`)

| Package | Required | Tested with | Purpose | License |
|---|---|---|---|---|
| [fastapi](https://pypi.org/project/fastapi/) | >= 0.110 | 0.162.0 | Local HTTP API | MIT |
| [uvicorn](https://pypi.org/project/uvicorn/) | >= 0.29 | 0.54.0 | Web server | BSD-3-Clause |
| [chess](https://pypi.org/project/chess/) (python-chess) | >= 1.10 | 1.11.2 | Move generation, PGN parsing, talking to Stockfish | GPL-3.0-or-later |
| [pywebview](https://pypi.org/project/pywebview/) | >= 5.0 | 6.2.1 | Native desktop window | BSD-3-Clause |
| [zstandard](https://pypi.org/project/zstandard/) | >= 0.22 | 0.25.0 | Reading the compressed Lichess puzzle file | BSD-3-Clause |

### Development and tests (only for running the tests: `pip install -e ".[dev]"`)

| Package | Required | Tested with | Purpose | License |
|---|---|---|---|---|
| [pytest](https://pypi.org/project/pytest/) | >= 8 | 9.2.0 | Test runner | MIT |
| [httpx](https://pypi.org/project/httpx/) | >= 0.27 | 0.28.1 | HTTP client for API tests | BSD-3-Clause |

### Installed automatically with the packages above

| Package | Tested with | Pulled in by | License |
|---|---|---|---|
| starlette | 1.10.0 | fastapi | BSD-3-Clause |
| pydantic | 2.14.3 | fastapi | MIT |
| pydantic-core | 2.50.4 | pydantic | MIT |
| annotated-types | 0.8.0 | pydantic | MIT |
| typing-inspection | 0.4.5 | fastapi, pydantic | MIT |
| annotated-doc | 0.0.5 | fastapi | MIT |
| opentelemetry-api | 1.45.2 | fastapi | Apache-2.0 |
| typing-extensions | 4.16.0 | several | PSF-2.0 |
| anyio | 4.16.0 | starlette, httpx | MIT |
| idna | 3.20 | anyio | BSD-3-Clause |
| click | 8.5.1 | uvicorn | BSD-3-Clause |
| h11 | 0.16.0 | uvicorn | MIT |
| bottle | 0.13.4 | pywebview | MIT |
| proxy-tools | 0.1.0 | pywebview | MIT |
| pythonnet | 3.2.1 | pywebview (Windows only) | MIT |
| clr-loader | 0.3.1 | pythonnet (Windows only) | MIT |
| cffi | 2.1.1 | clr-loader (Windows only) | MIT-0 |
| pycparser | 3.12 | cffi (Windows only) | BSD-3-Clause |
| pyobjc-* | — | pywebview (macOS only) | MIT |
| httpcore | 1.0.9 | httpx (dev) | BSD-3-Clause |
| certifi | 2026.10.23 | httpx (dev) | MPL-2.0 |
| iniconfig, pluggy, packaging, pygments, colorama | — | pytest (dev) | MIT / BSD / Apache-2.0 |

On Linux, the native window uses your distribution's PyGObject (see above) instead of pythonnet.

## JavaScript packages (`web/`)

### Bundled into the interface

| Package | Required | Tested with | Purpose | License |
|---|---|---|---|---|
| [@lichess-org/chessground](https://www.npmjs.com/package/@lichess-org/chessground) | ^10.5.0 | 10.5.0 | Chessboard, plus the *cburnett* piece set | GPL-3.0-or-later |
| [chess.js](https://www.npmjs.com/package/chess.js) | ^1.4.0 | 1.4.0 | Move validation in the browser | BSD-2-Clause |
| [uplot](https://www.npmjs.com/package/uplot) | ^1.6.32 | 1.6.32 | Eval graph and progress charts | MIT |
| [@fontsource-variable/hanken-grotesk](https://www.npmjs.com/package/@fontsource-variable/hanken-grotesk) | ^5.3.0 | 5.3.0 | Interface font | OFL-1.1 |
| [@fontsource-variable/fraunces](https://www.npmjs.com/package/@fontsource-variable/fraunces) | ^5.3.0 | 5.3.0 | Heading font | OFL-1.1 |

Fonts and all other assets are bundled locally, so the app never loads anything from the internet.

### Build tools (only needed to build the interface)

| Package | Required | Tested with | Purpose | License |
|---|---|---|---|---|
| [vite](https://www.npmjs.com/package/vite) | ^6.0.0 | 6.4.5 | Bundler and dev server | MIT |
| [typescript](https://www.npmjs.com/package/typescript) | ^5.6.0 | 5.9.3 | Type checking | Apache-2.0 |

Vite brings in esbuild, rollup, postcss and a few small helpers (all MIT, ISC or BSD).
`web/package-lock.json` pins the exact versions.

## Data

| Data | Included? | Purpose | License |
|---|---|---|---|
| ECO opening table ([lichess chess-openings](https://github.com/lichess-org/chess-openings)) | Yes, `content/eco/` | Naming openings and opening theory | CC0 |
| Lessons and endgame positions | Yes, `content/` | Lessons and endgame practice | Same as Chezz (GPL-3.0-or-later) |
| [Lichess puzzle database](https://database.lichess.org/#puzzles) | No (optional download) | Rated puzzles | CC0 |

## Checking what's installed

```bash
.venv/bin/python -m pip list        # Windows: .venv\Scripts\python -m pip list
cd web && npm ls --all              # every JavaScript package and version
```
