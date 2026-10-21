# Library rules — binds any agent that touches this folder

This folder is a **shelf** library (format: SPEC.md v1.0). The files ARE the
library: no database, no index server. Run `python3 shelf.py --help` for
commands (on Windows use `py`).

## Golden rule
Every file in `inbox/` must become an item under `items/<key>/` BEFORE it is
quoted, summarized, or answered from. Never answer from `inbox/` directly.

---

## A. Processing inbox/

Start with `python3 shelf.py doctor`: it lists inbox files not yet processed
and the extraction tools available.

### A1. PDFs
1. **Pick the key**: `<first-author-surname><year>-<first 3-4 title words>`,
   lowercase, e.g. `vaswani2017-attention-is-all-you` (look at page 1 for
   author/year). If the user has a BibTeX/Zotero key for it, use that instead.
2. **Create the item** with the metadata you can see:
   ```
   python3 shelf.py add inbox/file.pdf --key <key> --type paper \
       --title "..." --author "Surname, Given" --author "..." --year 2017
   ```
   This copies the PDF to `source.pdf`, hashes it, writes one page file per
   PDF page with pypdf text, reads printed page numbers if the PDF has them,
   and refuses duplicates.
3. **Improve extraction if a better tool exists** (priority: MinerU > marker > pypdf;
   see `~/TOOLS.md` if it exists). Always do this for math-heavy or scanned PDFs.
   - MinerU: run it, then
     `python3 shelf.py import-mineru <key> <out>/<name>_content_list.json`
   - marker (or anything else): rewrite each `pages/NNNN.md` body yourself,
     keep the frontmatter, set `source: marker`, put figures in `figures/`
     as `pNNNN-i.<ext>` and link them as `![fig pNNNN-i](../figures/pNNNN-i.png)`.
   - Spot-check a page or two against the PDF; fix garbled math by hand.
4. **Fill `toc.md`**: the `| Section | pdf pages |` table (replace the
   `shelf:todo` line). For books, add the pdf ↔ printed page relation at the
   top and set `page_offset` in item.yaml. Then run
   `python3 shelf.py apply-toc <key>`.
5. **Fill the rest of item.yaml** (authors, year, doi, tags).
6. **Prove alignment**: pick a sentence you can see on a specific page of
   the PDF and run
   `python3 shelf.py verify <key> <pdf_page> "<quote>" --mark`.
   It sets `status: verified` only if the item passes all checks; otherwise
   it tells you what is missing.
7. `python3 shelf.py lint <key>` must report no errors, then
   `python3 shelf.py catalog`.

### A2. Scans, photos, handwritten notes
1. Page images should be named `inbox/<key>-p001.jpg`, `-p002.jpg`, ...
2. `python3 shelf.py add-scan inbox/<key>-p*.jpg --key <key> --title "..."`
   creates pages with the image embedded and a `shelf:todo` marker.
3. Transcribe every page into its `pages/NNNN.md` (keep the image link at
   the top; replace the todo line). Math in LaTeX. Mark illegible words as
   `[illegible]` — never guess silently.
4. Set `status: extracted`, then continue with steps A1.4–A1.7.

### A3. Fixing mistakes
- Wrong key: `python3 shelf.py rekey <old> <new>` (never rename folders by hand).
- `doc-...` keys are provisional — rename them.

---

## B. Answering questions from the library

1. **Navigate**: `catalog.md` → the item's `toc.md` → the relevant pages.
   Search with grep over `items/*/pages/*.md` or
   `python3 shelf.py find "phrase"` (prints ready-made citations).
2. **Read the page files** you cite. Don't cite from memory or from the catalog.
3. **Cite every claim**:
   - `[key · p.N]` when the page has a `printed_page` (N = printed page)
   - `[key · pdf p.N]` otherwise (N = pdf page)
   - and always name the file: `items/<key>/pages/NNNN.md`
4. **Direct quotes**: check them with `python3 shelf.py verify <key> <pdf_page> "<quote>"`
   before presenting them as exact.
5. If the library doesn't contain the answer, say so. If you add outside
   knowledge, label it clearly as not from the library. Never invent page
   numbers.
6. Mention if a cited item is not `verified` (its page alignment is unproven).

---

## Never
- Edit, rename, or replace `source.pdf` or `figures/p*-full.*`
- Store processed material anywhere except `items/<key>/`
- Create files outside the SPEC.md layout
- Edit `catalog.md` by hand (regenerate it)
