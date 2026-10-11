# The Reader

Read code the way you'd explain it to someone. Point The Reader at a project folder and read it in layers, from what the project is for down to how one function works:

1. **Overview**: what the project does (from its README), what it's written in, where it starts (commands, `python -m`, scripts), what it depends on, and which files everything else leans on.
2. **Folder map**: the folders and files inside a folder, arranged top to bottom by who uses whom. Point at a box to see everything it connects to.
3. **File**: a call map of the file's functions, with the functions it calls in other files shown as dashed boxes you can click into.
4. **Function**: a flowchart built from the real code (branches, loops, `match`, `try/except`, early returns), next to the code and an explanation.

Calls are links everywhere: in flowcharts, in the code, in the explanations. Following one into another file keeps your place, and the browser's Back button takes you back. Every view has its own address, so reloading or bookmarking brings you back to the same spot.

Each layer has an instant explanation built from the code itself. Turn on AI explanations (Claude) for plain-English write-ups of the project, each folder, each file and each function. They're cached, so you only pay for code that changed.

You can still open a single file on its own: drop it in, paste it, or pass it on the command line.

Python only for now.

## Names

| What | Name |
| --- | --- |
| The project | The Reader |
| The folder, the pip package and the command you type | `the-reader` |
| The Python import (Python names can't contain `-`) | `thereader` |

## Install (EndeavourOS / Arch)

Run the install in the folder that contains `pyproject.toml`:

```bash
cd ~/Documents/github/The-Reader
pip install --break-system-packages -e .
```

pip will print `Defaulting to user installation`. That's expected: the `the-reader` command goes into `~/.local/bin`. If your shell then says `the-reader: command not found`, put that folder on your PATH once:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
```

`python -m thereader` works either way. Python 3.11+ is all you need to run it; the built interface ships inside the package.

## Use

```bash
the-reader ~/code/some-project      # read a whole project
the-reader path/to/file.py          # read one file
the-reader                          # start page: open a folder, or drop in a file
the-reader . --port 9000 --no-browser
```

You can also open a folder from the start page by typing or pasting its path; recent projects are listed there.

The first time you open a big project it's indexed (Django, about 3,000 Python files, takes a few seconds on a modern laptop). The result is cached in `~/.cache/the-reader/`, so opening it again is quick. After you change the code, use **Read the folder again** on the overview.

### AI explanations (optional)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export THE_READER_MODEL=claude-sonnet-5-5   # default; claude-haiku-5-5 is faster and cheaper
the-reader ~/code/some-project
```

Without a key everything except the AI write-ups works.

### Keys

| Key | Does |
| --- | --- |
| `j` / `k` | next / previous function in reading order |
| `m` / `f` | call map / flowchart |
| `u` | up one layer (function → file → folder → project) |
| `[` or Backspace | back |
| `/` or Ctrl+K | find a file or function |
| `Esc` | clear the highlighted lines |

Scroll moves the diagrams like a page; pinch or Ctrl/Cmd + scroll zooms.

## Project layout

```
The-Reader/                      project root: run pip install here
├── pyproject.toml               package info and the `the-reader` command
├── README.md
├── LICENSE.md                   PolyForm Noncommercial 1.0.0, with the credit lines
├── THIRD_PARTY_NOTICES.md       licenses of the libraries and fonts bundled in the interface
├── src/
│   └── thereader/               the Python app (import thereader)
│       ├── cli.py               the `the-reader` command
│       ├── __main__.py          makes `python -m thereader` work
│       ├── server.py            local web server and API
│       ├── explainer.py         function and file explanations, structural and AI
│       ├── analyzer/
│       │   ├── python_analyzer.py   outline, imports, calls, entry points
│       │   └── flowchart.py         turns a function body into a flowchart
│       ├── repo/                repo mode
│       │   ├── files.py         which files belong to the project, and their languages
│       │   ├── modules.py       module names; resolves imports and calls between files
│       │   ├── index.py         builds the project index: tree, links, entry points, README
│       │   ├── summaries.py     overview and folder explanations, structural and AI
│       │   ├── cache.py         on-disk cache of analysed files
│       │   ├── jobs.py          reads projects in the background, with progress
│       │   └── worker.py        runs in helper processes for big projects
│       ├── examples/            inventory.py and todo.py, to try it on
│       └── frontend_build/      the compiled interface (generated, don't edit)
└── frontend/                    interface source: React + TypeScript
    ├── scripts/                 build helpers (third-party notices)
    └── src/                     App, RepoWorkspace, FolderMap, RepoTree, Canvas, DetailPanel, …
```

## How it works

```
project folder ─► list files (git ls-files, or a walk that skips venvs, node_modules, caches)
               ─► analyse every Python file with tree-sitter (in parallel, cached by content)
               ─► module names (packages, src/ layout, folders without __init__.py)
               ─► resolve imports and calls between files ─► project index
                                                                  │
                         FastAPI ◄────────────────────────────────┘
                            │ JSON
                            ▼
   React 19 + React Flow 12, laid out with ELK and a layered folder layout, code highlighted by Shiki
```

Cross-file links handle plain imports, `from . import x`, relative and absolute imports, names re-exported through `__init__.py`, `from x import *`, methods inherited from classes in other files, and objects whose type is known from `x = Thing()`, `self.x = Thing()` or a `x: Thing` parameter.

## Changing the interface

Only needed if you edit files in `frontend/`. Node 20+:

```bash
sudo pacman -S nodejs npm
cd frontend && npm install
the-reader --no-browser &      # API on :8765
npm run dev                    # interface with hot reload, proxies /api to :8765
npm run build                  # compiles into src/thereader/frontend_build and refreshes THIRD_PARTY_NOTICES.md
```

## Limits

- Python only. Other languages slot in as another analyzer returning the same data.
- Links are worked out from the code, not by running it. A call on an object whose type can't be seen (an untyped parameter, a value returned from a function) is listed but not linked.
- In flowcharts, an exception can jump from anywhere in a `try` block to its `except`; that's drawn as one arrow, not one per statement.

## License

The Reader is © 2026 Ravada Siddharth ([Sidvortex](https://github.com/Sidvortex)) and is released under the **PolyForm Noncommercial License 1.0.0** (see [LICENSE.md](LICENSE.md)). In plain words:

- **Free for non-commercial use**: personal projects, study, research, teaching, hobby use, and use by charities, schools, public research bodies and government.
- **Credit the author**: anyone you give a copy to must also get the license and the `Required Notice` lines at the top of LICENSE.md, which name the author and this project.
- **No commercial use without permission**: using The Reader in or for a business, a paid product or a paid service needs written permission from the author. Ask by opening an issue on [GitHub](https://github.com/Sidvortex/The-Reader).

This summary is for convenience; LICENSE.md is what applies. The libraries and fonts bundled in the interface keep their own open-source licenses, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
