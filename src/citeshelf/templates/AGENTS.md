# Library rules — binds any agent that touches this folder

This folder is a **citeshelf** library (format: SPEC.md v2.1). The files ARE
the library: no database, no server. Commands: `shelf --help` (installed
with `uv tool install citeshelf`; if `shelf` isn't found, use `uvx --from
citeshelf shelf ...`).

```
inbox/   L1  the human's dump: messy, any folders, keeps growing
raw/     L2  originals, flat, renamed, never touched again
items/   L3  one folder per document, one md file per page: GROUND TRUTH
mapped/  L4  your organised, de-duplicated notes; every claim cites L3
```

## Golden rules
1. Never quote, summarise or answer from `inbox/` or `raw/`. Process first.
2. Everything you say from the library cites a page in `items/`.
3. L3 is a faithful copy of the document. Interpretation belongs in L4.
4. Never modify, rename or delete anything in `raw/`. Never delete from
   `inbox/` yourself: `shelf add` moves files safely.
5. **Log as you go, and be chatty.** Every transition leaves a written
   trail: `items/<key>/log.md` for L2 → L3, `mapped/log.md` for L3 → L4.
   Anything blurry, ambiguous, guessed or decided goes in the log, with the
   page (`p0003`). Write for an agent who arrives next month knowing
   nothing. Questions for the user go in as `- [ ] ...`.

## Every session: orient first
Read `mapped/README.md` and `mapped/subject.md`, then run
`shelf doctor`. It lists the tools to use, inbox files waiting,
items still pending, whether the subject interview has been done, and
**open questions** from the logs. Ask the user those questions early (batch
them in one message). Record each answer by ticking the line,
`- [x] ... -> answer`, and fix the pages or mapped files it affects
(logging that too).

## Tools
Local tools do the bulk reading so you don't spend tokens on every page.
There is exactly one extraction tool, **MinerU**, at the version citeshelf
pinned; page images and iPhone photos are handled by citeshelf itself.
- If `doctor` says MinerU isn't installed, run `shelf setup` yourself (no
  need to ask): it installs the pinned MinerU with uv, once per computer.
  If the user says MinerU is already installed somewhere (e.g. a project's
  `.venv`), use that instead: `shelf setup --use mineru=<path>`.
- **Use only citeshelf's commands for extraction.** Don't install or run
  other OCR/extraction programs, even ones you know.
- Without MinerU (too little RAM, or not installed yet), born-digital PDFs
  keep their plain pypdf text and you transcribe scans from page images.
- Office files: if `soffice` (LibreOffice) is available, convert with it;
  otherwise ask the user to export a PDF.
- Handwriting is never a tool's job: you transcribe it yourself from the
  images. Context and judgment matter more there than OCR.

---

## Step 1 — Understand the subject (first run, and when new kinds of material appear)
If `mapped/subject.md` still contains `<!-- shelf:todo -->`, interview the
user **before organising anything**. Keep it short (one message, plain
questions), e.g.:
- What subject/course is this, at what level, which semester?
- What's the syllabus or list of units (or where is it in the inbox)?
- How are you examined: format, marks, what do past papers look like?
- Which textbook(s) are the main reference?
- What's in each inbox folder? (list the folders `doctor` shows)
- Any notation or conventions you want kept?
- What do you want from this library: revision notes, exam prep, research?

Write the answers into `mapped/subject.md` (remove the todo marker). Later
agents read it instead of asking again. When a new inbox folder appears
that `subject.md` doesn't explain, ask about that folder only.

## Step 2 — Process the inbox (L1 → L2 → L3)

**2a. Plan.** From `doctor`'s list, decide what the *documents* are:
- one PDF = one document;
- several images = one document when they are pages of the same thing
  (same lecture, same exam paper). Look at the images; use names, dates and
  folders as hints. Decide the page order.
- duplicates (`DUPLICATE of raw/...`): tell the user and leave them.
- office files (`.docx`, `.pptx`): convert to PDF if `soffice` is available
  (`soffice --headless --convert-to pdf --outdir <scratch folder outside the
  library> <file>`), else ask the user to export a PDF.
- anything unclear: ask. One short question beats a wrong grouping.

Pick each key (SPEC.md §4.1): `vaswani2017-attention-is-all-you` for
published works; `notes-<course>-<what>`, `tut-<course>-week05`,
`exam-<course>-2023-endsem` for personal material.

**2b. Add each document.** This moves the originals from `inbox/` to `raw/`
(recording where they came from) and scaffolds the item:
```
shelf add "inbox/Sem 3/tutorials/tut 5.pdf" --key tut-ma201-week05 --type tutorial
shelf add inbox/books/hcv.pdf --key verma2019-concepts-of-physics --type book \
    --title "..." --author "Verma, H. C." --year 2019
shelf add inbox/pyqs/2022.pdf --key exam-ma201-2022-endsem --type exam --source scan
shelf add-scan "inbox/notes/IMG_2031.jpg" "inbox/notes/IMG_2032.jpg" \
    --key notes-ma201-lecture03 --title "Lecture 3: Fourier series"      # in page order
shelf add <scratch>/slides.pdf --key ... --converted-from inbox/x/slides.pptx
```

**2c. Convert every page faithfully** (the best tool available wins):
- **PDFs: let MinerU do it.** `shelf extract <key>` runs MinerU locally,
  one page at a time, for born-digital PDFs and scans alike (about 5–10 s a
  page; minutes for a long book: run it in the background if you can). If
  it stops or some pages fail (they keep their earlier text), run it again:
  it continues where it left off.
- **Then review cheaply**: read the text, not the images. For scans, run
  `shelf render <key>` and compare 1–2 pages against their
  images; open further images only where the text looks wrong (broken
  sentences, garbled math, missing lines). Fix what you find, log which
  pages you checked against the image.
- If no tool could read a page (it still has `shelf:todo`), `render` and
  transcribe that page from its image.
- Page images (`add-scan`) and handwriting: look at each image and
  transcribe it. iPhone HEIC photos can be passed to `add-scan` directly:
  the originals go to `raw/` and it links a readable JPG copy. Sideways
  photos are fine as they are; note it in the log.
- When you write a page yourself: keep the frontmatter, set `source:` to the
  tool (or `agent-transcription`), replace the `<!-- shelf:todo -->` line.
  Transcribe exactly what is there, in reading order: math in LaTeX,
  diagrams you can't express in text stay as images
  (`figures/pNNNN-i.png`, linked as `![fig pNNNN-i](../figures/pNNNN-i.png)`),
  unreadable words as `[illegible]`, a word or symbol you can guess as
  `[unclear: best reading]` (`[unclear: ma or mg]`). Don't fix the author's
  mistakes; you may add `<!-- agent: likely typo, x should be y -->`.
- **Write the conversion log** (`items/<key>/log.md`, append at the bottom;
  citeshelf has already logged origins and tools). One entry per session:
  ```markdown
  ## 2026-10-22 · agent-transcription (Claude)
  - Page order: from the numbers in the corners; IMG_2034 had none, placed
    3rd because it continues the derivation on p0002.
  - p0003: lower third blurry (phone shadow). Line 4 read as $\nabla \cdot E$,
    could be $\nabla \times E$; marked [unclear].
  - p0005: circuit diagram kept as figures/p0005-1.png; labels transcribed below.
  - p0006: margin note in different ink, probably added later; kept in place.
  - [ ] Is the symbol on p0003 line 4 a dot or a cross product?
  ```
  Log: how you decided grouping and page order; every `[unclear]` /
  `[illegible]` (lint warns if a marked page isn't mentioned); blurry,
  cut-off or rotated pages; diagrams kept as images; crossed-out or
  margin text; anything you guessed and why; tool problems (MinerU
  garbled a table on p0012, so you retyped it).
- When all pages are done, set `status: extracted` in item.yaml.

**2d. Finish each item.**
1. Fill `toc.md` (`| Section | pdf pages |` rows; for books add the
   pdf ↔ printed relation and set `page_offset`), then
   `shelf apply-toc <key>`.
2. Fill item.yaml metadata you can see (authors, year, doi, tags).
3. Prove page alignment: `shelf verify <key> <pdf_page> "<quote>" --mark`.
4. `shelf lint <key>` must show no errors.

## Step 3 — Build and update the map (L3 → L4)
Using `subject.md`, the inbox folders (`collection` in item.yaml) and the
content itself, organise everything in `mapped/` (SPEC.md §5):
- A hierarchy a student would want: units → topics, numbered in learning
  order (`mapped/02-thermodynamics/01-first-law.md`). Follow the syllabus
  if there is one. Past papers usually get their own section that maps
  each question to its topic file.
- **Union, not copies**: when lecture notes, a tutorial and the textbook
  cover the same idea, write it once and cite all of them.
- Definitions, theorems, formulas and exam questions: quoted word for word
  in blockquotes, with citations.
- Disagreements: `> **Sources differ:** ...` with both citations. Never pick
  one silently.
- Every content file starts with `title`, `sources`, `updated` frontmatter.
- Comment for the next agent with `<!-- agent: ... -->`: what the file
  covers, what's missing, what you weren't sure about.
- Books are already well structured: link to their `toc.md` and sections
  rather than copying them.

**On later runs, update incrementally**: for each new item, find the topic
files it belongs to (check `sources:` and `mapped/README.md`), merge it in,
add new files only for new topics. Don't rewrite what didn't change.

Then:
1. Update the layout in `mapped/README.md`.
2. **Write the mapping log**: append one entry to `mapped/log.md` per run.
   Explain your reasoning so the next agent (or the user) can follow and
   challenge it:
   ```markdown
   ## 2026-10-22 · run 3 · Claude
   **In:** inbox/tutorials/tut5.pdf -> tut-ma201-week05; 4 photos -> notes-ma201-lecture07
   **Changed:**
   - 03-fourier/02-convergence.md: added Dirichlet conditions from lecture 7
     (p0002); same wording as [kreyszig2011 · p.483], so cited both.
   **New:** 03-fourier/04-parseval.md (only source so far: tut5 pdf p.3)
   **Judgment calls:** Gibbs phenomenon kept under convergence, not its own
   topic: one page across all sources.
   **Sources differ:** lecture 7 p0003 uses period $2L$, tut5 uses $L$; both
   kept in 03-fourier/01-series.md.
   **Open questions:**
   - [ ] Is Parseval's theorem in your syllabus? Only the tutorial mentions it.
   ```
   Log every merge, every new or moved file, every ordering or grouping
   decision, every disagreement between sources, and anything you left out
   on purpose.
3. `shelf lint` must show no errors: every citation in `mapped/`
   must resolve to a real page.
4. `shelf catalog`.

---

## Answering questions
1. **Navigate** with `mapped/` (README → topic file). For exact wording
   go to the cited L3 pages. Search with
   `shelf find "phrase"` or grep `items/*/pages/*.md`.
2. **Read the page files you cite.** Don't cite from memory or from mapped/
   alone: mapped/ tells you where to look; items/ is the evidence.
3. **Cite every claim**: `[key · p.N]` when the page has a printed page,
   else `[key · pdf p.N]`, and name the file `items/<key>/pages/NNNN.md`.
4. **Check direct quotes** with `shelf verify <key> <pdf_page> "<quote>"`.
5. Not in the library? Say so. Outside knowledge? Label it as such.
   Never invent page numbers. Mention if a cited item isn't `verified`.

## Fixing mistakes
Logs are append-only: never delete or rewrite an entry; add a new one that
says what was wrong and what you changed.
- Wrong key: `shelf rekey <old> <new>` (updates mapped/ too).
- Bad grouping or extraction: save the item's `log.md`, delete the item
  folder, add it again from `raw/` (e.g. `shelf add raw/<file>.pdf --key ...`;
  the manifest keeps its history), then paste the old log entries at the top
  of the new `log.md` and add an entry saying why it was redone.

## Never
- Modify, rename or delete files in `raw/`
- Delete files from `inbox/` yourself
- Put processed material anywhere but `items/<key>/` and `mapped/`
- Summarise or "improve" text in L3 pages
- Edit `catalog.md` or `raw/manifest.yaml` by hand
- Delete or rewrite log entries
- Silently guess a blurry word: mark it `[unclear: ...]` and log it
