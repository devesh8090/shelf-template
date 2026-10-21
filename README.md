# shelf

**Give your AI agent a library it can cite to the exact page.**

shelf is a folder convention for academics who need answers drawn from
*their own* books, papers and notes, with every claim traceable to a page
you can open and check:

> The paper attributes the transformer's parallelism to replacing recurrence
> entirely with attention [vaswani2017-attention-is-all-you · p.2]
> (`items/vaswani2017-attention-is-all-you/pages/0003.md`).

It works with Claude Code, Codex, Cursor, Gemini CLI, or any agent that can
read files. There is no database, no server, no embeddings: just a folder
layout ([SPEC.md](SPEC.md)), a rules file agents read
([AGENTS.md](AGENTS.md)), and one small Python script (`shelf.py`).

---

## Why not just give the PDF to the AI?

You can, once. It breaks down when you rely on it:

| Asking an AI about a PDF directly | shelf |
|---|---|
| Large books don't fit; the AI skims and guesses | One file per page; the agent reads only page 148 |
| Every new chat re-reads the same PDF | Extract once, reuse forever |
| Page numbers are often wrong (pdf page vs printed page, or invented) | Every page file records both; `verify` proves quotes are on the page |
| Each session organizes things differently | One standard layout; any agent, any session, any collaborator |
| You can't check where a claim came from | Citations point to real files anyone can open |

**Why not a classic RAG / vector database?** Those chunk text by token
count and match on embedding similarity. That suits huge corpora, but
provenance gets lost and nobody can audit it. For a personal or lab library
(tens to a few thousand documents), shelf's page files *are* the chunks,
split at the boundary a citation needs.

## Quickstart

**1. Get the template.** Click **Use this template** on GitHub, or download
this folder anywhere (name it `library/`, `papers/`, `books/`, ...).

**2. Install the two dependencies** (Python 3.9+):

```bash
pip install -r requirements.txt        # pypdf + pyyaml
python3 shelf.py doctor                # checks everything is ready
```

Optional: install [MinerU](https://github.com/opendatalab/MinerU) for
high-quality extraction of math, tables and figures. Without it, plain
text extraction still works; you just get less clean layout.

Have other extraction tools (a new OCR model, a conda env, a GPU server)?
Describe them once in `~/TOOLS.md` in your home folder; agents read it
before extracting. For example:

```markdown
# Extraction tools on this machine
- MinerU 2.x: `mineru -p <pdf> -o <out>` (best for papers with math)
- olmOCR in conda env "ocr": `conda run -n ocr python -m olmocr ...` (use for scans)
- No GPU, so prefer pypdf for long plain-text books
```

Tools only affect how clean the extracted text is. Page numbers and
citations work the same with any of them, including plain pypdf.

**3. Add documents.** Drop PDFs (or photos of handwritten notes) into
`inbox/`, open your agent in this folder, and say:

> Process inbox/ per AGENTS.md.

The agent picks a key for each document, extracts every page, fills in
metadata and a table of contents, and proves the page numbering is right.

**4. Ask questions.**

> What does Goodfellow say about the vanishing gradient problem? Cite pages.
>
> Compare how my lecture notes and the textbook define entropy.
>
> Find every place in my library that discusses Bayesian priors.

Answers come back with `[key · p.N]` citations and file paths.

## What a processed item looks like

```
items/vaswani2017-attention-is-all-you/
  source.pdf          the original, never modified
  item.yaml           title, authors, year, DOI, sha256, status, ...
  toc.md              section → pdf page ranges
  pages/
    0001.md           one markdown file per PDF page
    0002.md
    ...
  figures/
    p0003-1.png       extracted figures, linked from their page
```

and a page file:

```markdown
---
key: vaswani2017-attention-is-all-you
pdf_page: 3
printed_page: 2
section: 2 Background
source: mineru
---
# 2 Background

The goal of reducing sequential computation also forms the foundation of ...
$\mathrm{Attention}(Q,K,V) = \mathrm{softmax}(QK^T/\sqrt{d_k})V$

![fig p0003-1](../figures/p0003-1.png)
```

See [`items/_example-attention/`](items/_example-attention/) for a complete
(structural) example.

## Using it inside an existing project

Put the library in a subfolder, e.g. `my-thesis/library/`. Agents read rules
from the folder they work in, so add one line to your **project's**
`CLAUDE.md` / `AGENTS.md`:

```markdown
Source material lives in library/ — follow library/AGENTS.md when reading,
adding to, or citing from it.
```

Run commands from anywhere: `python3 library/shelf.py find "some phrase"`.

## Commands

The agent normally runs these for you; they're here so you can too.

```bash
python3 shelf.py doctor                     # deps, unprocessed inbox files, item statuses
python3 shelf.py add inbox/x.pdf --key smith2020-deep-learning-for \
        --title "..." --author "Smith, Jane" --year 2020 --type paper
python3 shelf.py add-scan inbox/notes-p*.jpg --key lecture3-notes   # photos/scans
python3 shelf.py import-mineru <key> <mineru-out>/x_content_list.json
python3 shelf.py apply-toc <key>            # copy toc.md sections into pages
python3 shelf.py find "exact phrase"        # → [key · p.N]  items/key/pages/NNNN.md
python3 shelf.py verify <key> 12 "quote"    # is the quote really on pdf page 12?
python3 shelf.py lint                       # check every item against SPEC.md
python3 shelf.py catalog                    # regenerate catalog.md
python3 shelf.py bib > library.bib          # BibTeX for your reference manager
python3 shelf.py rekey <old> <new>          # rename an item safely
```

Quote matching ignores case, line breaks, hyphenation, ligatures (ﬁ) and
curly quotes, so a quote copied from a PDF viewer still verifies.

## Item status

| status | meaning |
|---|---|
| `pending` | created, pages not yet extracted (e.g. scans awaiting transcription) |
| `partial` | some pages failed to extract |
| `extracted` | all pages have content |
| `verified` | metadata and table of contents filled, and a quote has been proven on its page |

Only `verified` items guarantee that page numbers are right. Agents are told
to mention when they cite anything less.

## FAQ

**Which AI agents work?** Any agent that reads files and runs shell commands.
`AGENTS.md` is read natively by Codex, Cursor, and others; `CLAUDE.md`
points Claude Code at it. For other tools, tell the agent to read `AGENTS.md` first.

**Can I commit my library to git?** Extracted text and metadata, yes.
`.gitignore` excludes `source.pdf` and `inbox/` by default because PDFs are
large and often copyrighted. Remember that text extracted from a
copyrighted book is still copyrighted: keep repos with it private.

**Can I share a library with my lab?** Yes. Share the folder or a private
repo. Each item's `sha256` identifies the exact PDF, so colleagues can drop
in their own copy and know it matches.

**How big can it get?** Searching with grep stays fast for thousands of
documents. Past that, you want a real search index.

**What about synonyms / "find the concept, not the words"?** grep matches
words, not meaning. Agents handle this by navigating: catalog → table of
contents → pages, then reading. For most academic questions that works
better than similarity search, because the agent reads whole pages in
context.

**Does it handle handwritten notes?** Yes: `add-scan` keeps the original
images and the agent transcribes each page (`source: agent-transcription`).
Always spot-check transcriptions of notes you'll rely on.

**I use Zotero.** Use your Zotero/Better BibTeX citation key as the shelf
key (`--key vaswani2017attention`), and `shelf.py bib` round-trips metadata
back to BibTeX.

## Contributing

The **spec** ([SPEC.md](SPEC.md)) is the product; `shelf.py` is one
implementation of it. Issues and PRs are welcome, especially:
- extraction importers (marker, Docling, Nougat, ...),
- reports of PDFs that extract badly,
- other implementations of the spec (R, Zotero plugin, editor extensions).

Spec changes follow [SPEC.md §9](SPEC.md#9-versioning): additive changes
only within 1.x.

Development:

```bash
pip install -r requirements.txt pytest
pytest -q
```

## Changelog

- **v0.3.1 / spec 1.1**: page `source:` accepts any tool name; `~/TOOLS.md`
  documented for describing your own extraction tools.
- **v0.3 / spec 1.0**: written spec (`SPEC.md`); new commands `find`,
  `lint`, `add-scan`, `import-mineru`, `apply-toc`, `rekey`, `bib`;
  `verify --mark` sets `verified`; printed page numbers read from PDF page
  labels; duplicate detection by sha256; atomic `add`; quote matching
  tolerant of hyphenation, curly quotes and spacing; figure links fixed to
  `../figures/`; `page` prints to stdout.
- **v0.2**: argparse CLI, utf-8 everywhere, encrypted-PDF handling,
  `status: partial`, best-effort image extraction, smoke tests + CI.
- **v0.1**: initial template.

## License

MIT for the template and script. Your documents stay yours.
