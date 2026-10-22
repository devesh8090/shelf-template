# citeshelf format — specification v2.1

This document defines the **citeshelf** library format: a folder layout that
turns a messy pile of academic material (books, papers, slides, tutorials,
past exam papers, photos of handwritten notes) into a library any AI agent
or person can navigate and **cite down to the exact page**.
citeshelf (the `shelf` command) is the reference implementation; anything that produces and
reads this layout is a conforming implementation.

The key words MUST, SHOULD and MAY are used as in RFC 2119.

---

## 1. The four levels

```
<library>/
  inbox/      L1  the human drops anything here, in any folder structure
  raw/        L2  every original file, flat, renamed, never modified
  items/      L3  one folder per document, one markdown file per page
  mapped/     L4  organised, de-duplicated notes built from L3
  .citeshelf    marks the library root: `citeshelf: <version>`, `spec: "<version>"`
  AGENTS.md  CLAUDE.md  SPEC.md   the agent's rules, from the citeshelf version above
  catalog.md                      generated index of items
```

Each level is built only from the level before it:

| level | written by | content | trust |
|---|---|---|---|
| L1 `inbox/` | human | anything, any structure | unprocessed: never cited or answered from |
| L2 `raw/` | script | the originals, byte for byte | evidence of what was received |
| L3 `items/` | script + agent | faithful page-by-page conversion | **ground truth: all citations point here** |
| L4 `mapped/` | agent (+ human) | topics, definitions, links between sources | a map: every claim cites L3 |

- After L3 exists, agents work from L3 and MUST NOT re-read L2, except to
  deliberately re-extract an item (e.g. with a better OCR tool).
- Folders in `items/` whose name starts with `_` are **examples** and
  folders starting with `.` are **scratch**; tools MUST ignore both.
- A library MAY live inside another project (e.g. `thesis/library/`).
  All paths in this spec are relative to the library root.

## 2. L1 — inbox/

- The human MAY use any subfolders and file names, and keeps adding to them
  over time. The folder a file was dropped into (e.g. `Sem 3/tutorials`) is
  context and is recorded as the item's `collection`.
- Processing a file **moves** it out of `inbox/` into `raw/`; folders are
  kept. An empty inbox means everything has been processed.
- A file is deleted from `inbox/` only after its copy in `raw/` has been
  written and its SHA-256 checked.
- A file whose SHA-256 is already in `raw/manifest.yaml` is a duplicate. It
  stays in `inbox/` until the human decides.

## 3. L2 — raw/

- Flat: no subfolders. Files are named after the item they belong to:
  - one file: `<key>.<ext>` (e.g. `tut01-fourier-series.pdf`)
  - page images: `<key>-p001.<ext>`, `<key>-p002.<ext>`, ... in page order
  - the office file a PDF was converted from: `<key>.<ext>` (e.g. `.pptx`)
- Raw files are **never** modified, renamed or deleted. When an item is
  renamed, its raw files keep their names.
- `raw/manifest.yaml` records every raw file:

  ```yaml
  files:
    tut01-fourier-series.pdf:
      from: inbox/Sem 3/tutorials/tutorial 1.pdf   # where it was dropped
      sha256: 9f2c...
      added: '2026-10-22'
      item: tut01-fourier-series                   # current owner
  ```

## 4. L3 — items/

```
items/<key>/
  item.yaml        metadata
  toc.md           section -> pdf page ranges
  log.md           conversion log: how these pages were made, what was unclear
  pages/NNNN.md    one markdown file per page, 0001.md ... NNNN.md
  figures/         figures (pNNNN-i.ext) and page images (pNNNN-full.png)
```

An item folder MUST contain `item.yaml`, `toc.md`, `log.md`, `pages/` and
`figures/`, and SHOULD contain nothing else.

### 4.1 Keys

- A key MUST match `^[a-z0-9]+(-[a-z0-9]+)*$` and MUST equal the folder name.
- Published works SHOULD use `<first-author-surname><year>-<first 3-4 title words>`
  (`vaswani2017-attention-is-all-you`), or the user's BibTeX/Zotero key, lowercased.
- Personal material SHOULD use `<kind>-<course>-<what>`, e.g.
  `notes-thermo-lecture03`, `tut-ma201-week05`, `exam-ma201-2023-endsem`.
- Keys starting with `doc-` are provisional (generated without input) and
  SHOULD be renamed.
- On collision a numeric suffix is appended: `-2`, `-3`, ...
- Renaming (`shelf rekey`) MUST update `item.yaml`, every page's `key:`,
  the manifest's `item:` fields and every reference in `mapped/`.

### 4.2 item.yaml

Required fields:

| field | type | meaning |
|---|---|---|
| `key` | string | same as the folder name |
| `title` | string | full title |
| `authors` | list | `"Surname, Given"`, in order; `[]` if unknown or personal notes |
| `type` | enum | `paper` `book` `chapter` `thesis` `notes` `slides` `tutorial` `assignment` `exam` `cheatsheet` `misc` |
| `year` | int or empty | publication year (or exam year) |
| `source` | enum | `original` (born-digital), `scan`, `photocopy` |
| `raw` | list | the raw files this item was built from, in page order (`raw/<name>`) |
| `sha256` | hex | of the raw file; for several page images, the SHA-256 of their concatenated lowercase hex SHA-256s, in order |
| `status` | enum | see §4.6 |
| `pages_pdf` | int | number of page files |
| `page_offset` | int or empty | `printed_page = pdf_page - page_offset` for the main body |
| `added` | date | `YYYY-MM-DD` |
| `tags` | list | free-form |

Optional: `collection` (inbox folder it came from), `converted_from`
(`raw/<key>.pptx` etc.), `extract_failures`, and BibTeX fields `doi`, `url`,
`journal`, `booktitle`, `publisher`, `volume`, `number`, `pages`, `edition`,
`isbn`. Other fields MAY be added; tools MUST preserve fields they don't know.

### 4.3 Page files

`pages/NNNN.md`: `NNNN` is the 1-based **pdf page** (position in the file
or image sequence, not the number printed on the page), zero-padded to 4
digits, contiguous from `0001.md` to `pages_pdf`.

```yaml
---
key: notes-thermo-lecture03
pdf_page: 3
printed_page: 2         # as printed (int, or "xiv"); empty if unnumbered
section: 2 First law    # from toc.md, best effort; may be empty
source: mineru          # the tool that produced this page
---
```

`source` is free text naming the tool, lowercase, optionally with a version
(`mineru`, `marker`, `pypdf`, `olmocr@0.3`, ...). Reserved values:
`agent-transcription` (an AI agent read the page image) and `manual` (typed
by a person). It MUST NOT be empty.

The body is the page's content as markdown:
- a **faithful conversion**: no summarising, no reordering, no corrections;
- uncertain readings are marked inline: `[unclear: best reading]` (or
  `[unclear: ma or mg]`) when you can guess, `[illegible]` when you can't.
  Every page with such a marker MUST be explained in `log.md` (§4.5);
- math as LaTeX (`$...$`, `$$...$$`), tables as markdown or HTML;
- figures that cannot be converted to text are kept as images, linked
  relative to the page file: `![fig p0003-1](../figures/p0003-1.png)`;
- scanned pages start with the page image: `![page 3](../figures/p0003-full.png)`
  or, for image items, `![page 3](../../../raw/<key>-p003.jpg)`;
- unfinished pages contain the marker `<!-- shelf:todo -->`.

Text that continues across a page break is split at the break.

### 4.4 toc.md

A markdown table whose **last column** is a pdf page or range (`3`, `3-4`, `3–4`):

```markdown
# Concepts of Physics, Vol. 2
pdf ↔ printed: printed = pdf - 12

| Section | pdf pages |
|---|---|
| 23 Heat and Temperature | 13–30 |
| 23.1 Hot and cold bodies | 13–14 |
```

Nested sections are separate rows; the narrowest range wins when filling
`section:` (`shelf apply-toc`).

### 4.5 log.md (conversion log)

A chronological, deliberately chatty record of how the item's pages were
made, appended at the bottom, one `## <date> · <who/what>` entry at a time.
citeshelf writes its own entries (where each raw file came from, tools,
failures, renames); agents add what only they know:

```markdown
## 2026-10-22 · agent-transcription (Claude)
- Page order: from the numbers in the top-right corners; IMG_2034 had none,
  placed 3rd because it continues the derivation on p0002.
- p0003: lower third blurry (phone shadow). Line 4 read as $\nabla \cdot E$,
  could be $\nabla \times E$; marked [unclear].
- p0005: circuit diagram kept as figures/p0005-1.png; labels transcribed below it.
- [ ] Is the symbol on p0003 line 4 a dot or a cross product?
```

- Every page with `[unclear: ...]` or `[illegible]` MUST be mentioned by its
  page id (`p0003`).
- Questions for the user are task-list items `- [ ] ...`. When answered they
  are ticked with the answer, `- [x] ... -> answer`, and the page is fixed.
- Entries are never deleted or rewritten; corrections are new entries.

### 4.6 Status

| status | meaning |
|---|---|
| `pending` | created; pages still need OCR/transcription (`shelf:todo`) |
| `partial` | extracted, but some pages failed (`extract_failures`) |
| `extracted` | every page has its content; no `shelf:todo` in pages |
| `verified` | extracted **and** metadata filled (authors/year for published works) **and** `toc.md` filled **and** at least one quote confirmed on its page |

`shelf verify --mark` sets `verified` only when these conditions hold.

## 5. L4 — mapped/

The agent's organised view of the whole library: material from all items
merged by topic, in the order the subject is learned, redundancy removed.

### 5.1 Entry files (always present at `mapped/` root)

| file | content |
|---|---|
| `README.md` | start here: what the library covers and the full layout of `mapped/`, one line per file, in reading order |
| `subject.md` | the subject context from the interview with the user (course, syllabus, exams, textbooks, what each inbox folder holds, notation) |
| `log.md` | the mapping log (§5.3) |

### 5.2 Content files

Any hierarchy of folders and `.md` files the agent finds sensible (e.g.
`mapped/02-thermodynamics/01-first-law.md`, `mapped/exams/by-topic.md`).
Number folders and files (`01-`, `02-`) to fix the reading order.
Each content file starts with:

```yaml
---
title: First law of thermodynamics
sources: [notes-thermo-lecture03, hcv2-concepts-physics-vol2]   # items it draws on
updated: 2026-10-22
---
```

Rules:
1. **Every claim cites L3**: `[key · p.N]` or `[key · pdf p.N]` (§6). Every
   cited key MUST be listed in `sources`.
2. **Definitions, theorems, formulas and exam questions are quoted word for
   word**, as blockquotes, with their citation.
3. **Merge, don't drop**: when sources overlap, write the content once and
   cite every source that supports it.
4. **Never silently resolve disagreements**: mark them
   `> **Sources differ:** ...` with both citations.
5. **Comments for the next agent** go in HTML comments `<!-- agent: ... -->`,
   which stay invisible when the markdown is rendered.
6. L4 is derived: it MAY be reorganised or rebuilt from L3 at any time.

### 5.3 mapped/log.md (mapping log)

The L3 → L4 counterpart of the conversion log: one chatty entry per run,
appended at the bottom, explaining the agent's reasoning so the next agent
(or the human) can follow and challenge it:

```markdown
## 2026-10-22 · run 3 · Claude
**In:** inbox/tutorials/tut5.pdf -> tut-ma201-week05; 4 photos -> notes-ma201-lecture07
**Changed:**
- 03-fourier/02-convergence.md: added Dirichlet conditions from lecture 7
  (p0002), same wording as the textbook [kreyszig2011 · p.483], so cited both.
- 03-fourier/README.md: new subsection order follows the syllabus (subject.md).
**New:** 03-fourier/04-parseval.md (first source covering it: tut5 pdf p.3)
**Judgment calls:** put Gibbs phenomenon under convergence, not a topic of
its own: only one page across all sources.
**Sources differ:** lecture 7 p0003 defines the period as $2L$, tut5 as $L$;
both kept in 03-fourier/01-series.md with a note.
**Open questions:**
- [ ] Is Parseval's theorem in your syllabus? Only the tutorial mentions it.
```

Same question convention as §4.5; entries are never deleted or rewritten.

`shelf lint` checks that every citation in `mapped/` resolves to an
existing page and that `sources` matches what is cited. Citations inside
`code spans` and fenced code blocks are treated as examples and not checked.

## 6. Citations

Any answer or L4 note drawing on the library MUST cite like this:

```
[key · p.N]         when printed_page is known  (N = printed page)
[key · pdf p.N]     otherwise                  (N = pdf page)
```

In answers, also name the file: `items/<key>/pages/NNNN.md`. A claim that
cannot be pointed to a page file MUST say so instead of giving a page.

## 7. Invariants

- Files in `raw/` are never modified, renamed or deleted.
- Nothing is cited or answered from `inbox/` or `raw/` directly.
- L3 pages are faithful conversions; interpretation belongs in L4.
- Every L4 claim resolves to an L3 page.

## 8. Versioning

The spec version is `MAJOR.MINOR`. Minor versions only add optional fields
or values; breaking changes bump the major version.

- 2.1: a library is marked by a `.citeshelf` file and no longer contains
  the program; AGENTS.md/CLAUDE.md/SPEC.md come from the citeshelf release
  (`shelf init`, `shelf upgrade`). Library contents are unchanged.
- 2.0: four levels. Originals move from `items/<key>/source.pdf` to the
  flat `raw/` with `manifest.yaml`; inbox files are moved, not copied; new
  `mapped/` level; conversion log `items/<key>/log.md` and mapping log
  `mapped/log.md`; `[unclear: ...]`/`[illegible]` markers; `raw`/`collection`
  item fields; types `tutorial`, `assignment`, `exam`.
- 1.1: page `source` is free text.
- 1.0: initial version.
