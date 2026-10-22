# shelf

**Dump your messy course material in a folder. Get an organised, page-cited
library your AI agent can study from.**

shelf is a folder convention for students and academics. You throw in
everything (textbooks, papers, slides, tutorials, past exam papers, phone
photos of handwritten notes) in whatever folders you like, and keep
adding to it every week. An AI agent converts each document page by page,
then organises all of it into clean topic notes where **every line cites
the exact page it came from**:

> **Definition (First law).** "The heat supplied to a system equals the
> increase in its internal energy plus the work done by it."
> [verma2019-concepts-of-physics · p.31] [notes-phy201-lecture04 · pdf p.2]

It works with Claude Code, Codex, Cursor, Gemini CLI, or any agent that
reads files. No database, no server, no embeddings: a folder layout
([SPEC.md](SPEC.md)), a rules file agents follow ([AGENTS.md](AGENTS.md)),
and one small Python script (`shelf.py`).

---

## How it works: four levels

```
 inbox/   you dump anything        Sem 3/tutorials/tut 5.pdf, notes/IMG_2031.jpg, pyqs/2022.pdf ...
   │        agent moves files out (inbox empty = everything processed)
 raw/     originals, flat, renamed tut-ma201-week05.pdf, notes-ma201-lecture03-p001.jpg ...
   │        one-to-one conversion: MinerU / OCR / agent transcription
 items/   one folder per document  pages/0001.md ... + figures/ + log.md      ← ground truth
   │        agent organises using your answers + the folders + the content
 mapped/  topic notes, merged      02-thermodynamics/01-first-law.md         ← cites items/
```

| level | what's in it | who writes it |
|---|---|---|
| **inbox/** | anything, in any folders; you keep adding | you |
| **raw/** | the original files, byte for byte; never touched again | the script |
| **items/** | each document converted faithfully, **one markdown file per page**, with images of figures that can't be text | the agent + script |
| **mapped/** | everything merged by topic, in learning order, duplicates removed, definitions quoted word for word | the agent, from your answers |

Why split it like this:
- **items/ is the evidence.** It's a faithful copy, so a citation like
  `[key · pdf p.3]` points to a real file (`items/key/pages/0003.md`) that
  anyone can open and check against the original.
- **mapped/ is the map.** Your five sets of notes, the tutorials and the
  textbook overlap heavily; mapped/ merges them into one set of topic
  notes and keeps citing all of them. If it ever gets messy, it can be
  rebuilt from items/.
- **New files slot in.** Add next week's tutorial to the inbox and only
  that file is converted; only the topic notes it touches are updated.

## Quickstart

**1. Get the template.** Click **Use this template** on GitHub, or copy this
folder anywhere (name it after your course: `ma201/`, `thesis-library/`, ...).

**2. Install the two dependencies** (Python 3.9+):

```bash
pip install -r requirements.txt        # pypdf + pyyaml
python3 shelf.py setup                 # picks and installs extraction tools for this PC
```

`setup` is optional (the agent runs it on first use anyway); see
[Extraction tools](#extraction-tools). Without any tools shelf still works:
scans are then transcribed by the agent from images.

**3. Dump your material** into `inbox/`, in any folders you like:

```
inbox/
  textbook/HC Verma vol 2.pdf
  lecture notes/IMG_2031.jpg  IMG_2032.jpg  IMG_2033.jpg ...
  tutorials/tut1.pdf  tut2.pdf
  pyqs/2022 endsem.pdf  2023-q1.png  2023-q2.png
```

**4. Open your agent in the folder and say:**

> Process inbox/ per AGENTS.md.

The first time, the agent asks a few questions about your course (syllabus,
exams, what each folder is), then converts everything and builds
`mapped/`. Next week, drop new files in and say the same thing again.

**5. Ask questions and demand page citations.**

> Explain the first law using my lecture notes and the textbook. Cite pages.
>
> Which past-paper questions are on Fourier series, and where are the answers in my notes?
>
> Give me every definition from unit 2, word for word.

## Extraction tools

Reading every page is the expensive part for an AI agent. shelf hands that
to local tools on your computer, and the agent only reviews the result.

`python3 shelf.py setup` runs once per computer (the agent runs it for you
on first use):

1. It reads your hardware and puts it in a tier: **high** (NVIDIA GPU with
   6 GB+ or Apple silicon with 16 GB+), **mid** (8 GB+ RAM) or **low**.
2. For each job it picks the first tool in `tools.registry.json` that fits.
3. It installs what's missing and saves the picks in `~/.shelf/tools.json`.
   **Every shelf library on that computer uses the same tools**; running
   `setup` again keeps them (`--refresh` picks again).

```
machine: linux, 15.0 GB RAM, 12 CPUs, GPU: NVIDIA GeForce GTX 1650 (4.0 GB) -> tier mid
needs administrator rights; the user runs once:
  sudo apt-get install -y pipx
after that, run `python3 shelf.py setup` again for: mineru
tools on this computer (/home/you/.shelf/tools.json):
  pdf-text  pypdf          mineru not installed yet
  scan-ocr  ocrmypdf       mineru not installed yet
  render    pdftoppm       backup: mutool
  office    libreoffice
  images    imagemagick
```

| job | candidates, best first (minimum tier) |
|---|---|
| `pdf-text` | MinerU (mid), pypdf (built in) |
| `scan-ocr` | MinerU (mid), OCRmyPDF, Tesseract |
| `render` | pdftoppm, mutool |
| `office` | LibreOffice |
| `images` | ImageMagick, heif-convert |

Then `python3 shelf.py extract <key>` runs the right tool on a document
locally and fills its pages; the agent reads the text and spot-checks a
page or two against the images instead of transcribing everything.
MinerU (version 4) runs one page at a time through its local server, which
`extract` starts and stops for you (about 5–10 s a page on a mid-tier
laptop); an interrupted run continues where it stopped, and a page MinerU
fails on keeps its earlier text. Nothing is uploaded.

- Installs that don't need admin rights (pipx, brew, winget, conda) just
  run. Ones that do (apt, dnf, pacman) are batched into one command: in a
  terminal `setup` asks for your password once; run by an agent, it prints
  the line for you. MinerU downloads PyTorch and models (several GB) on
  first use.
- `python3 shelf.py setup --dry-run` shows the plan without changing anything.
- Python tools are installed with `uv tool install` (or `pipx`) when you have
  uv/pipx: their own environment, on your PATH, no admin rights. Already
  have a tool inside some project's virtual environment? Point shelf at it
  instead of installing again:
  `python3 shelf.py setup --use mineru=/path/to/.venv/bin/mineru`.
- The candidate list is data (`tools.registry.json`). Ask the agent to look
  for newer tools: it proposes changes and edits only with your OK.
- Your own tool (a GPU server, a conda env)? Add it under `overrides` in
  `~/.shelf/tools.json`, e.g. `"pdf-text": {"use": "mineru"}` to force a pick.
- Handwriting is transcribed by the agent itself: context and judgment beat
  local OCR there.

The tools only affect how clean the text is. Page numbers and citations
work the same with any of them.

## Using it inside an existing project

Put the library in a subfolder, e.g. `my-thesis/library/`, and add one line
to your project's `CLAUDE.md` / `AGENTS.md`:

```markdown
Source material lives in library/ — follow library/AGENTS.md when reading,
adding to, or citing from it.
```

## Commands

The agent runs these for you; they're listed so you can too.

```bash
python3 shelf.py doctor                  # tools, what's waiting in inbox/, what needs work
python3 shelf.py setup                   # pick + install one tool per job for this computer
python3 shelf.py tools                   # this computer's tools (~/.shelf/tools.json)
python3 shelf.py extract <key>           # run the local tool on a document, fill its pages
python3 shelf.py add "inbox/tutorials/tut 5.pdf" --key tut-ma201-week05 --type tutorial
python3 shelf.py add inbox/pyqs/2022.pdf --key exam-ma201-2022 --type exam --source scan
python3 shelf.py add-scan inbox/notes/IMG_1.jpg inbox/notes/IMG_2.jpg --key notes-ma201-l03
python3 shelf.py render <key>            # page images of a scanned PDF, for transcription
python3 shelf.py apply-toc <key>         # copy toc.md sections into the pages
python3 shelf.py find "exact phrase"     # → [key · p.N]  items/key/pages/NNNN.md
python3 shelf.py verify <key> 12 "quote" # is the quote really on pdf page 12?
python3 shelf.py lint                    # check items + every citation in mapped/
python3 shelf.py catalog                 # regenerate catalog.md
python3 shelf.py bib > library.bib       # BibTeX for your reference manager
python3 shelf.py rekey <old> <new>       # rename an item everywhere
```

Quote matching ignores case, line breaks, hyphenation, ligatures (ﬁ) and
curly quotes, so a quote copied from a PDF viewer still verifies.

## Safety

- **Nothing is lost.** A file leaves `inbox/` only after its copy in `raw/`
  is written and checked by hash. `raw/manifest.yaml` records where every
  file came from (`inbox/Sem 3/tutorials/tut 5.pdf`).
- **Duplicates are caught.** The same file dropped twice (under any name)
  is flagged and left in the inbox for you to decide.
- **Citations are checked.** `lint` fails if any citation in `mapped/` points
  to a page that doesn't exist. `verify` proves a quote is on its page.
- **Every judgment call is written down.** Each document has a conversion
  log (`items/<key>/log.md`): which tool, how the page order was decided,
  every blurry or ambiguous spot by page. `mapped/log.md` records each
  organising run: what was merged and why, where sources disagree. Doubtful
  readings are marked in the text as `[unclear: ma or mg]`, and questions
  for you (`- [ ] ...`) are collected by `doctor` so the next session asks
  them.
- **Status tells you what to trust.** `pending` (needs transcription) →
  `extracted` → `verified` (page alignment proven). Agents mention when
  they cite something not yet verified.

## FAQ

**Can I commit my library to git?** `items/` and `mapped/` are text, so yes.
`.gitignore` excludes `inbox/` and `raw/` by default: originals are large
and often copyrighted. Text extracted from a copyrighted book is still
copyrighted, so keep such repos private.

**How accurate is handwriting transcription?** Good with today's models,
not perfect. The original image stays embedded above each transcribed
page, unclear words are marked `[illegible]`, and you can spot-check any
page. Check the definitions you'll rely on in an exam.

**How big can it get?** grep stays fast for thousands of documents, which is
far more than a course or a thesis needs.

**Why not a vector database / classic RAG?** Those split text by token
count and match by similarity: fine for huge corpora, but you can't see
where an answer came from. Here the pages are the chunks, and every claim
links to one.

**Which agents work?** Any agent that reads files and runs commands.
`AGENTS.md` is read natively by Codex, Cursor and others; `CLAUDE.md`
points Claude Code at it. For other tools, tell the agent to read
`AGENTS.md` first.

**I use Zotero.** Use your Zotero/Better BibTeX citation key as the item key
(`--key vaswani2017attention`); `shelf.py bib` exports BibTeX.

## Contributing

The **spec** ([SPEC.md](SPEC.md)) is the product; `shelf.py` is one
implementation. Issues and PRs are welcome, especially importers for more
extraction tools, reports of documents that convert badly, and other
implementations of the spec.

```bash
pip install -r requirements.txt pytest
pytest -q
```

## Changelog

- **v0.5**: local tools do the reading. `shelf.py setup` reads the hardware
  tier (low/mid/high), picks one tool per job from `tools.registry.json`,
  installs it (admin installs batched into one command) and saves the picks
  per computer in `~/.shelf/tools.json`, kept across projects; finds tools
  in `~/.local/bin` and conda folders too, or anywhere via `setup --use`;
  installs Python tools with `uv tool`/pipx. `shelf.py extract` runs
  MinerU 4 (page by page via its local server, resumable) / OCRmyPDF /
  Tesseract locally and fills the pages; agents review instead of
  transcribing. Replaces `~/TOOLS.md` and `import-mineru` (MinerU 2.x).
  `add-scan` takes iPhone HEIC photos; `render` falls back to mutool.
- **v0.4 / spec 2.0**: four levels. `inbox/` files are moved (safely) into a
  flat `raw/` with `manifest.yaml`; new `mapped/` level with entry files,
  subject interview and citation checking in `lint`; inbox subfolders
  recorded as `collection`; `render` for scanned PDFs; `--converted-from`
  for office files; types `tutorial`, `assignment`, `exam`; `rekey` updates
  `mapped/`; conversion and mapping logs, `[unclear: ...]` markers, open
  questions collected by `doctor`. Not compatible with 1.x layouts (`items/<key>/source.pdf`).
- **v0.3.1 / spec 1.1**: page `source:` accepts any tool name; `~/TOOLS.md`.
- **v0.3 / spec 1.0**: written spec; `find`, `lint`, `add-scan`,
  `import-mineru`, `apply-toc`, `rekey`, `bib`; `verify --mark`.
- **v0.2**: argparse CLI, utf-8, encrypted PDFs, tests + CI.
- **v0.1**: initial template.

## License

MIT for the template and script. Your documents stay yours.
