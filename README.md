# citeshelf

**Dump your messy course material in a folder. Get an organised, page-cited
library your AI agent can study from.**

citeshelf is for students and academics. You throw in everything
(textbooks, papers, slides, tutorials, past exam papers, phone photos of
handwritten notes) in whatever folders you like, and keep adding to it
every week. Your AI agent converts each document page by page, then
organises all of it into clean topic notes where **every line cites the
exact page it came from**:

> **Definition (First law).** "The heat supplied to a system equals the
> increase in its internal energy plus the work done by it."
> [verma2019-concepts-of-physics · p.31] [notes-phy201-lecture04 · pdf p.2]

It works with Claude Code, Codex, Cursor, Gemini CLI, or any agent that
reads files and runs commands. No database, no server, no embeddings: a
folder layout ([SPEC.md](src/citeshelf/templates/SPEC.md)), rules the agent
follows ([AGENTS.md](src/citeshelf/templates/AGENTS.md)), and the `shelf`
command for everything that should be exact.

---

## Quickstart

**1. Install [uv](https://docs.astral.sh/uv/getting-started/installation/)**
(one command on Windows, macOS and Linux), then citeshelf:

```bash
uv tool install git+https://github.com/devesh8090/citeshelf
```

**2. Make a library** (one per course, thesis, or project):

```bash
shelf init ma201
cd ma201
shelf setup        # once per computer: installs MinerU for reading PDFs locally
```

**3. Dump your material** into `inbox/`, in any folders you like:

```
inbox/
  textbook/HC Verma vol 2.pdf
  lecture notes/IMG_2031.jpg  IMG_2032.HEIC ...
  tutorials/tut1.pdf  tut2.pdf
  pyqs/2022 endsem.pdf  2023-q1.png
```

**4. Open your agent in the folder and say:**

> Process inbox/ per AGENTS.md.

The first time, the agent asks a few questions about your course (syllabus,
exams, what each folder is), then converts everything and builds `mapped/`.
Next week, drop new files in and say the same thing again.

**5. Ask questions and demand page citations.**

> Explain the first law using my lecture notes and the textbook. Cite pages.
>
> Which past-paper questions are on Fourier series, and where are the answers in my notes?
>
> Give me every definition from unit 2, word for word.

## How it works: four levels

```
 inbox/   you dump anything        Sem 3/tutorials/tut 5.pdf, notes/IMG_2031.jpg, pyqs/2022.pdf ...
   │        moved out once processed (inbox empty = everything processed)
 raw/     originals, flat, renamed tut-ma201-week05.pdf, notes-ma201-lecture03-p001.jpg ...
   │        one-to-one conversion: MinerU locally, the agent for handwriting
 items/   one folder per document  pages/0001.md ... + figures/ + log.md      ← ground truth
   │        agent organises using your answers + the folders + the content
 mapped/  topic notes, merged      02-thermodynamics/01-first-law.md         ← cites items/
```

| level | what's in it | who writes it |
|---|---|---|
| **inbox/** | anything, in any folders; you keep adding | you |
| **raw/** | the original files, byte for byte; never touched again | `shelf` |
| **items/** | each document converted faithfully, **one markdown file per page**, figures kept as images | `shelf` + MinerU + the agent |
| **mapped/** | everything merged by topic, in learning order, duplicates removed, definitions quoted word for word | the agent, from your answers |

- **items/ is the evidence.** A faithful copy, so a citation like
  `[key · pdf p.3]` points to a real file (`items/key/pages/0003.md`) that
  anyone can open and check against the original.
- **mapped/ is the map.** Your five sets of notes, the tutorials and the
  textbook overlap heavily; mapped/ merges them into one set of topic notes
  and keeps citing all of them. It can always be rebuilt from items/.
- **New files slot in.** Next week's tutorial is converted on its own, and
  only the topic notes it touches are updated.

## Reading happens on your computer

Reading every page is the expensive part for an AI agent, so citeshelf hands
it to local tools and the agent only reviews the result:

| job | done by |
|---|---|
| PDFs and scans: text, math (LaTeX), tables | **MinerU**, locally, one page at a time (`shelf extract`) |
| page images, iPhone HEIC photos | citeshelf itself |
| handwriting | the agent: context and judgment beat OCR there |
| office files | LibreOffice if you have it; otherwise the agent asks for a PDF |

- **One tool, pinned.** citeshelf uses exactly one extraction tool, at the
  version it was tested with (`shelf setup` installs it with uv: no admin
  rights). A newer or better tool arrives as a new citeshelf release, never
  as a surprise on your machine.
- **About 5–10 s a page** on an ordinary laptop. MinerU's first run
  downloads its models (several GB). `extract` starts and stops MinerU's
  local server for you, continues where it stopped if interrupted, and a
  page MinerU fails on keeps its earlier text. Nothing is uploaded.
- **Under 8 GB of RAM?** `setup` skips MinerU: PDFs keep their plain text
  and the agent reads scans itself (more tokens, same result).
- **Already have MinerU** in some virtual environment?
  `shelf setup --use mineru=/path/to/.venv/bin/mineru`.

## Updating

```bash
uv tool upgrade citeshelf
shelf upgrade          # in each library: refreshes AGENTS.md / SPEC.md
```

Your library's content never changes on upgrade. If you edited AGENTS.md
yourself, your version is kept next to it as `AGENTS.md.old`.

## Using it inside an existing project

`shelf init library` inside, say, your thesis repo, and add one line to the
project's own `CLAUDE.md` / `AGENTS.md`:

```markdown
Source material lives in library/ — follow library/AGENTS.md when reading,
adding to, or citing from it.
```

`shelf` finds the library from any folder inside it, or takes
`--library PATH`.

## Commands

The agent runs these for you; they're listed so you can too.

```bash
shelf init [PATH]                  # make a folder a library
shelf doctor                       # tools, what's waiting in inbox/, what needs work
shelf setup                        # install MinerU for this computer (once)
shelf tools                        # what this computer uses for each job
shelf add "inbox/tutorials/tut 5.pdf" --key tut-ma201-week05 --type tutorial
shelf add inbox/pyqs/2022.pdf --key exam-ma201-2022 --type exam --source scan
shelf add-scan inbox/notes/IMG_1.jpg inbox/notes/IMG_2.HEIC --key notes-ma201-l03
shelf extract <key>                # MinerU fills the pages, locally
shelf render <key>                 # page images, for the agent to transcribe from
shelf apply-toc <key>              # copy toc.md sections into the pages
shelf find "exact phrase"          # → [key · p.N]  items/key/pages/NNNN.md
shelf verify <key> 12 "quote"      # is the quote really on pdf page 12?
shelf lint                         # check items + every citation in mapped/
shelf catalog                      # regenerate catalog.md
shelf bib > library.bib            # BibTeX for your reference manager
shelf rekey <old> <new>            # rename an item everywhere
shelf upgrade                      # refresh this library's AGENTS.md / SPEC.md
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
The library's `.gitignore` excludes `inbox/` and `raw/` by default:
originals are large and often copyrighted. Text extracted from a
copyrighted book is still copyrighted, so keep such repos private.

**How accurate is it?** MinerU is very good on printed text, tables and
math, but not perfect (it once read the hex `0x10FFFF` as
`0 \times 10FFFF`). Handwriting depends on your handwriting. That's why the
agent spot-checks pages against their images, marks doubtful words
`[unclear: ...]`, and logs what it checked. Verify the definitions you'll
rely on in an exam.

**How big can it get?** grep stays fast for thousands of documents, which
is far more than a course or a thesis needs.

**Why not a vector database / classic RAG?** Those split text by token
count and match by similarity: fine for huge corpora, but you can't see
where an answer came from. Here the pages are the chunks, and every claim
links to one.

**Which agents work?** Any agent that reads files and runs commands.
`AGENTS.md` is read natively by Codex, Cursor and others; `CLAUDE.md`
points Claude Code at it. For other tools, tell the agent to read
`AGENTS.md` first.

**I use Zotero.** Use your Zotero/Better BibTeX citation key as the item key
(`--key vaswani2017attention`); `shelf bib` exports BibTeX.

## Contributing

The format ([SPEC.md](src/citeshelf/templates/SPEC.md)) is the product; the
`shelf` command is one implementation. Issues and PRs are welcome,
especially reports of documents that convert badly.

```bash
git clone https://github.com/devesh8090/citeshelf && cd citeshelf
uv run pytest -q
uv run shelf --help
```

## Changelog

- **v0.6 / spec 2.1**: citeshelf is a package: `uv tool install`, then
  `shelf init` makes any folder a library (marked by `.citeshelf`; no copy
  of the program inside), `shelf upgrade` refreshes a library's rules.
  One pinned extraction tool, MinerU 4.0.12, installed by `shelf setup`
  with uv (no admin rights); page images and HEIC photos built in
  (pypdfium2, pillow-heif). Removes the tool registry, hardware tiers,
  OCRmyPDF/Tesseract and system package-manager installs.
- **v0.5**: local tools do the reading: `setup`, `tools`, `extract`
  (MinerU 4, OCRmyPDF, Tesseract), per-computer tool picks.
- **v0.4 / spec 2.0**: four levels (inbox → raw → items → mapped), conversion
  and mapping logs, `[unclear: ...]` markers, open questions in `doctor`.
- **v0.3 / spec 1.x**: written spec; `find`, `lint`, `add-scan`,
  `apply-toc`, `rekey`, `bib`; `verify --mark`.
- **v0.2**: argparse CLI, utf-8, encrypted PDFs, tests + CI.
- **v0.1**: initial template.

## License

MIT for citeshelf. Your documents stay yours.
