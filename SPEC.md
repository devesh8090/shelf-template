# shelf format — specification v1.1

This document defines the **shelf** library format: a folder layout that lets
any AI agent (or person) find, read, and cite documents down to the exact page.
`shelf.py` is the reference implementation; anything that produces and reads
this layout is a conforming implementation.

The key words MUST, SHOULD and MAY are used as in RFC 2119.

---

## 1. Library layout

```
<library>/
  SPEC.md            this file (spec version lives in its title)
  AGENTS.md          workflow rules for agents
  catalog.md         generated index of items (do not edit by hand)
  inbox/             unprocessed input files
  items/<key>/       one folder per document ("item")
    item.yaml        metadata
    source.pdf       the original PDF (absent for image scans)
    toc.md           section -> pdf page ranges
    pages/NNNN.md    one markdown file per page, 0001.md ... NNNN.md
    figures/         images extracted from or belonging to pages
```

- An item folder MUST contain `item.yaml`, `toc.md`, `pages/` and `figures/`,
  and SHOULD contain nothing else.
- Folders in `items/` whose name starts with `_` are **examples** and folders
  starting with `.` are **scratch**; tools MUST ignore both.
- A library MAY live inside another project (e.g. `thesis/library/`).
  All paths in this spec are relative to the library root.

## 2. Keys

- A key MUST match `^[a-z0-9]+(-[a-z0-9]+)*$` and MUST equal the item's folder name.
- A key SHOULD be `<first-author-surname><year>-<first 3-4 title words>`,
  e.g. `vaswani2017-attention-is-all-you`. An existing BibTeX/Zotero citation
  key, lowercased, is an equally good choice (`vaswani2017attention`).
- Keys generated without human/agent input start with `doc-` and are
  provisional; they SHOULD be renamed (`shelf.py rekey`).
- On collision a numeric suffix is appended: `-2`, `-3`, ...
- A key is the item's permanent identity. Renaming MUST update `item.yaml`
  and the `key:` field of every page file.

## 3. item.yaml

Required fields:

| field | type | meaning |
|---|---|---|
| `key` | string | same as the folder name |
| `title` | string | full title |
| `authors` | list of strings | `"Surname, Given"`, in order; `[]` if unknown |
| `type` | enum | `paper` `book` `chapter` `thesis` `notes` `slides` `cheatsheet` `misc` |
| `year` | int or empty | publication year |
| `sha256` | hex string | see below |
| `source` | enum | `original` (born-digital), `scan`, `photocopy` |
| `status` | enum | see §6 |
| `pages_pdf` | int | number of page files |
| `page_offset` | int or empty | `printed_page = pdf_page - page_offset` for the main body; empty if unknown or irregular |
| `added` | date | `YYYY-MM-DD` |
| `tags` | list of strings | free-form |

Optional fields (used for BibTeX export): `doi`, `url`, `journal`,
`booktitle`, `publisher`, `volume`, `number`, `pages`, `edition`, `isbn`,
and `extract_failures` (list of pdf pages that failed extraction).
Other fields MAY be added; tools MUST preserve fields they do not understand.

**sha256** is the SHA-256 of `source.pdf`. For image-scan items it is the
SHA-256 of the concatenated lowercase hex SHA-256s of the page images in page
order. It identifies the document across libraries and detects duplicates.

## 4. Page files

`pages/NNNN.md` — `NNNN` is the 1-based **pdf page** (the page index in the
PDF file, not the number printed on the page), zero-padded to 4 digits.
Files MUST be contiguous from `0001.md` to `pages_pdf`.

Each file starts with YAML frontmatter:

```yaml
---
key: vaswani2017-attention-is-all-you
pdf_page: 3
printed_page: 2              # as printed (int, or string like "xiv"); empty if unnumbered/unknown
section: 2 Background        # from toc.md, best effort; may be empty
source: mineru               # the tool that produced this page (see below)
---
```

`source` is free text naming the tool, lowercase, optionally with a version:
`mineru`, `marker`, `pypdf`, `olmocr@0.3`, ... Reserved values:
`agent-transcription` (an AI agent read the page image) and `manual` (typed
by a person). It MUST NOT be empty.

The body is the page's content as markdown:
- math as LaTeX (`$...$`, `$$...$$`), tables as markdown or HTML;
- each figure on the page as an image link **relative to the page file**:
  `![fig p0003-1](../figures/p0003-1.png)`, followed by its caption if any;
- unfinished pages contain the marker `<!-- shelf:todo -->`.

Content that continues across a page break is split at the break; do not
move text to the page where its paragraph started.

## 5. Figures and toc.md

- Figures are named `p<NNNN>-<i>.<ext>` (i = 1, 2, ... in reading order).
- Full-page images of scanned pages are named `p<NNNN>-full.<ext>`.
- `toc.md` maps sections to pdf pages using a markdown table whose **last
  column** is a page or range (`3`, `3-4`, `3–4`):

  ```markdown
  # Attention Is All You Need
  pdf ↔ printed: printed = pdf - 1

  | Section | pdf pages |
  |---|---|
  | 1 Introduction | 2 |
  | 2 Background | 3–4 |
  ```

  Nested sections are listed as separate rows; the narrowest range wins
  when filling `section:` (`shelf.py apply-toc`). For books, state the
  pdf ↔ printed page relation above the table.

## 6. Status lifecycle

| status | meaning |
|---|---|
| `pending` | scaffolded; pages not yet extracted/transcribed |
| `partial` | extracted, but some pages failed (`extract_failures`) |
| `extracted` | every page file has its content; no `shelf:todo` markers in pages |
| `verified` | extracted **and** metadata (authors, year) filled **and** `toc.md` filled **and** at least one quote confirmed on its page |

Only `verified` items guarantee page alignment. `shelf.py verify --mark`
sets `verified` only when these conditions hold.

## 7. Citations

Any answer drawing on the library MUST cite like this:

```
[key · p.N]         when printed_page is known  (N = printed page)
[key · pdf p.N]     otherwise                  (N = pdf page)
```

and MUST name the file it came from: `items/<key>/pages/NNNN.md`.
A claim that cannot be pointed to a page file MUST say so instead of
giving a page number.

## 8. Invariants

- `source.pdf` and `figures/p*-full.*` are the originals: never edited,
  renamed or replaced. (Moving the whole item folder during `rekey` is allowed.)
- Processed material lives only under `items/<key>/`.
- Nothing is answered from `inbox/` directly.

## 9. Versioning

The spec version is `MAJOR.MINOR`. Minor versions only add optional fields
or values; a library valid under 1.x stays valid under every later 1.y.
Breaking changes bump the major version.

- 1.1: page `source` is free text (was a fixed list).
- 1.0: initial version.
