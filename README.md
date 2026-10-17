# The Reader

Read code the way you'd explain it to someone. Point The Reader at a project folder or a GitHub link and read it in layers, from what the project is for down to how one function works:

1. **Overview**: what the project does (from its README), what it's written in, where it starts (commands, `python -m`, scripts), what it depends on, and which files everything else leans on.
2. **Folder map**: the folders and files inside a folder, arranged top to bottom by who uses whom. Point at a box to see everything it connects to.
3. **File**: a call map of the file's functions, with the functions it calls in other files shown as dashed boxes you can click into.
4. **Function**: a flowchart built from the real code (branches, loops, `match`, `try/except`, early returns), next to the code and an explanation.

Calls are links everywhere: in flowcharts, in the code, in the explanations. Following one into another file keeps your place, and the browser's Back button takes you back. Every view has its own address, so reloading or bookmarking brings you back to the same spot.

Each layer has an instant explanation built from the code itself. Turn on AI explanations (with an API key from any AI service) for plain-English write-ups of the project, each folder, each file and each function. They're cached, so you only pay for code that changed.

Click **Export** to save the whole explanation as one PDF or Word file, to read offline, print or share: the overview first, then each folder, each file and each function, with a contents page and bookmarks.

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
the-reader ~/code/some-project                  # read a whole project
the-reader https://github.com/pallets/flask     # read a public project on GitHub
the-reader path/to/file.py                      # read one file
the-reader                                      # start page: open a folder or link, or drop in a file
the-reader . --port 9000 --no-browser
```

You can also open a folder or a GitHub link from the start page by typing or pasting it; recent projects are listed there.

### GitHub links

Paste any of these:

| Link | Opens |
| --- | --- |
| `https://github.com/owner/repo` (or `owner/repo`) | the default branch |
| `…/tree/<branch or tag>` | that branch or tag |
| `…/tree/<branch>/some/folder` | that folder |
| `…/blob/<branch>/path/to/file.py` | that file |
| a link with a commit hash instead of a branch | that exact commit |

The Reader downloads just that commit (no history) into `~/.cache/the-reader/github/`, keeps the most recently used few, and reuses them. Only public repositories work from a link; for a private one, clone it and open the folder. Each project's address names the exact commit (`#/r/gh~owner~repo~<commit>/…`), so a copied address opens the same code for someone else, even after The Reader restarts.

The first time you open a big project it's indexed (Django, about 3,000 Python files, takes a few seconds on a modern laptop). The result is cached in `~/.cache/the-reader/`, so opening it again is quick. After you change the code, use **Read the folder again** on the overview.

### AI explanations (optional)

Click **Add key** at the top of the page and paste an API key. Keys from OpenAI, Google Gemini, Anthropic, Groq, Mistral, DeepSeek, xAI and OpenRouter work, and The Reader works out which service a key is from. The key stays in your browser. Under **Options** you can pick the service yourself, choose a model, or point to your own AI server (anything that speaks the OpenAI chat format, such as Ollama; local use only).

Or set a key for the whole server before starting it:

```bash
export THE_READER_AI_KEY=your-key
the-reader ~/code/some-project
```

Without a key everything except the AI write-ups works.

### Save as a document

**Export** at the top of a project makes a PDF or a Word (.docx) file of its explanation. Choose the whole project or just the folder you're in, and how deep to go: folders, files, or every class and function. Tests and examples are left out unless you untick that. AI explanations you've already opened are used; everything else is the explanation worked out from the code, so exporting never spends anything on AI.

Big projects are cut off after 5,000 sections (1,500 on a public server) to keep the file readable; export one folder at a time to get the rest. For PDFs The Reader uses the DejaVu fonts when they're installed (`sudo pacman -S ttf-dejavu`), which cover most languages; without them it falls back to simpler built-in fonts.

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

## Put it online

The Reader can run as a website where anyone pastes a GitHub link or drops in a file. Start it in **public mode**: `the-reader --public`, or set `THE_READER_PUBLIC=1`. In public mode:

- visitors can open GitHub links and single files, but never folders on the server;
- AI explanations use each visitor's own API key (the **Add key** button). The key stays in their browser and goes to the server only with explanation requests, which use it once and don't keep it;
- the server's own key (`THE_READER_AI_KEY`), if you set one, pays for at most `THE_READER_AI_DAILY_LIMIT` new explanations a day across all visitors (0 unless you set it). Explanations already made are cached and cost nothing;
- limits suit a small server: one project read at a time, up to 2,500 Python files each, two projects kept in memory.

The `Dockerfile` builds that website (Python 3.13, git, public mode, port from `$PORT`), so hosts that build from a Dockerfile, such as Render or Railway, can run it straight from this repository:

```bash
docker build -t the-reader .
docker run --rm -p 8765:8765 the-reader          # then open http://localhost:8765
```

| Variable | Public default | What it does |
| --- | --- | --- |
| `THE_READER_PUBLIC` | `1` in the Dockerfile | public mode |
| `THE_READER_AI_KEY` | not set | the server's own key, from any supported service (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and the like are also read) |
| `THE_READER_AI_PROVIDER` | detected | `openai`, `google`, `anthropic`, `groq`, `mistral`, `deepseek`, `xai`, `openrouter` or `custom` |
| `THE_READER_AI_DAILY_LIMIT` | `0` | new AI explanations a day that the server's key pays for |
| `THE_READER_MODEL` | the service's default | the model for AI explanations |
| `THE_READER_AI_BASE_URL` | not set | address of your own AI server (provider `custom`) |
| `THE_READER_MAX_PY_FILES` | `2500` | Python files read per project |
| `THE_READER_MAX_OPEN` | `2` | projects kept in memory (a Django-sized one needs about 250 MB) |
| `THE_READER_MAX_BUSY` | `1` | projects read at the same time |
| `THE_READER_WORKERS` | `1` | helper processes for reading big projects |
| `THE_READER_KEEP_REPOS` | `6` | downloaded repositories kept on disk |
| `THE_READER_EXPORT_MAX_SECTIONS` | `1500` | most sections in an exported document (5,000 on your own computer) |
| `THE_READER_EXPORTS_AT_ONCE` | `1` | documents made at the same time |
| `THE_READER_MAX_DOWNLOAD_MB` / `THE_READER_MAX_CHECKOUT_MB` | `250` / `500` | biggest repository it will download / unpack |

On a server with more memory, raise `THE_READER_MAX_OPEN`, `THE_READER_MAX_BUSY` and `THE_READER_WORKERS`.

## Project layout

```
The-Reader/                      project root: run pip install here
├── pyproject.toml               package info and the `the-reader` command
├── README.md
├── LICENSE.md                   PolyForm Noncommercial 1.0.0, with the credit lines
├── THIRD_PARTY_NOTICES.md       licenses of the libraries and fonts bundled in the interface
├── Dockerfile                   runs The Reader as a website (public mode)
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
│       │   ├── github.py        GitHub links: which commit, a shallow download, size limits
│       │   ├── modules.py       module names; resolves imports and calls between files
│       │   ├── index.py         builds the project index: tree, links, entry points, README
│       │   ├── summaries.py     overview and folder explanations, structural and AI
│       │   ├── cache.py         on-disk cache of analysed files
│       │   ├── jobs.py          reads projects in the background, with progress
│       │   └── worker.py        runs in helper processes for big projects
│       ├── export/              PDF and Word export of a project's explanation
│       │   ├── build.py         collects the sections in reading order
│       │   ├── md.py            reads the explanations' markdown
│       │   ├── pdf.py           writes the PDF (reportlab)
│       │   └── word.py          writes the .docx (python-docx)
│       ├── examples/            inventory.py and todo.py, to try it on
│       └── frontend_build/      the compiled interface (generated, don't edit)
└── frontend/                    interface source: React + TypeScript
    ├── scripts/                 build helpers (third-party notices)
    └── src/                     App, RepoWorkspace, FolderMap, RepoTree, Canvas, DetailPanel, …
```

## How it works

```
GitHub link ─► resolve the branch, tag or commit ─► download that one commit (no history)
                                                                  │
project folder ◄──────────────────────────────────────────────────┘
               ─► list files (git ls-files, or a walk that skips venvs, node_modules, caches)
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
- GitHub links work for public repositories only, and need `git` installed.

## License

The Reader is © 2026 Ravada Siddharth ([Sidvortex](https://github.com/Sidvortex)) and is released under the **PolyForm Noncommercial License 1.0.0** (see [LICENSE.md](LICENSE.md)). In plain words:

- **Free for non-commercial use**: personal projects, study, research, teaching, hobby use, and use by charities, schools, public research bodies and government.
- **Credit the author**: anyone you give a copy to must also get the license and the `Required Notice` lines at the top of LICENSE.md, which name the author and this project.
- **No commercial use without permission**: using The Reader in or for a business, a paid product or a paid service needs written permission from the author. Ask by opening an issue on [GitHub](https://github.com/Sidvortex/The-Reader).

This summary is for convenience; LICENSE.md is what applies. The libraries and fonts bundled in the interface keep their own open-source licenses, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
