#!/usr/bin/env python3
"""shelf.py - reference implementation of the shelf library format (v0.4).

Four levels (SPEC.md); the agent workflow is in AGENTS.md.
  L1 inbox/   the human dumps anything here, in any folders
  L2 raw/     every original, flat, renamed, never modified (+ manifest.yaml)
  L3 items/   one folder per document, one markdown file per page: ground truth
  L4 mapped/  the agent's organised, de-duplicated notes, citing L3

Commands:
  init                              create the folders and mapped/ templates
  doctor                            deps, waiting inbox files, raw/items/mapped health
  add <file.pdf> --key K [...]      L1->L2->L3 for a PDF (moves it out of inbox/)
  add-scan <img...> --key K [...]   same for page images, in the order given
  render <key>                      page images of a scanned PDF, for transcription
  import-mineru <key> <content_list.json>
                                    replace page text with MinerU output
  apply-toc <key>                   copy sections from toc.md into page frontmatter
  rekey <old> <new>                 rename an item everywhere (raw names stay)
  page <key> <n>                    print path of pages/NNNN.md
  verify <key> <pdf_page> "quote"   is this quote really on that page?
  find "quote" [--key K]            which pages contain it? (prints citations)
  lint [key...]                     check items, and every citation in mapped/
  catalog                           regenerate catalog.md
  bib [key...]                      print BibTeX entries

The agent does the judgment work (grouping, metadata, toc, transcription,
mapped/); this script does the deterministic parts. Deps: pypdf, pyyaml.
Run any command with --help. On Windows use `py` instead of `python3`."""
import argparse, hashlib, json, re, shutil, subprocess, sys, unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

__version__ = "0.4.0"
SPEC_VERSION = "2.0"

ROOT = Path(__file__).resolve().parent
INBOX, RAW, ITEMS, MAPPED = (ROOT / "inbox", ROOT / "raw", ROOT / "items",
                             ROOT / "mapped")
UTF = "utf-8"

STATUSES = ("pending", "partial", "extracted", "verified")
TYPES = ("paper", "book", "chapter", "thesis", "notes", "slides", "tutorial",
         "assignment", "exam", "cheatsheet", "misc")
SOURCES = ("original", "scan", "photocopy")
REQUIRED = ("key", "title", "authors", "type", "year", "source", "raw", "sha256",
            "status", "pages_pdf", "page_offset", "added", "tags")
PAGE_FIELDS = ("key", "pdf_page", "printed_page", "section", "source")
BIB_FIELDS = ("doi", "url", "journal", "booktitle", "publisher", "volume",
              "number", "pages", "edition", "isbn")
BIBTYPE = {"paper": "article", "book": "book", "chapter": "incollection",
           "thesis": "phdthesis"}
KEY_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CITE_RE = re.compile(r"\[([a-z0-9]+(?:-[a-z0-9]+)*) · (pdf )?p\.\s?([^\]\s,;]+)")
TODO = "<!-- shelf:todo -->"
UNCLEAR_RE = re.compile(r"\[(?:unclear|illegible)\b[^\]]*\]")
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
OFFICE_EXT = {".doc", ".docx", ".ppt", ".pptx", ".odt", ".odp", ".rtf"}
ENTRY_FILES = ("README.md", "subject.md", "log.md")  # mapped/ files with no sources

# what PDFs print vs. what people type when they copy a quote
PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", "−": "-", "­": ""})

MANIFEST_HEAD = ("# raw/ manifest: one entry per original file.\n"
                 "# Written by shelf.py -- do not edit by hand.\n")

MAPPED_TEMPLATES = {
    "README.md": f"""# mapped/ — start here

{TODO} The agent fills this in on the first `Process inbox/` run.

This is **level 4** of the library: the content of every document in
`items/`, organised by topic in the order the subject is learned, with
duplication removed. Agents write it for humans *and* for the next agent.
Every fact here cites a page in `items/`; that is the ground truth, and
this folder is only the map.

## Layout

(agent: list every folder and file here, one line each, in reading order)

## Conventions

- Citations `[key · p.N]` / `[key · pdf p.N]` resolve to `items/<key>/pages/NNNN.md`.
- Definitions, theorems and formulas are quoted word for word.
- `> **Sources differ:**` marks places where sources disagree.
- `<!-- agent: ... -->` comments are notes for future agents.
""",
    "subject.md": f"""# Subject context

{TODO} Filled in by the agent from a short interview with the user on the
first run. Future agents: read this before organising anything.

- **Subject / course:**
- **Institution, level, semester:**
- **Syllabus or list of units:**
- **Exams (format, marks, what is examined):**
- **Main textbook(s):**
- **What each inbox folder contains:**
- **Notation or conventions to keep:**
- **What the user wants from this library:**
- **Language(s) of the material:**
""",
    "log.md": """# Mapping log

<!-- Chronological: append one entry per run at the bottom (format in
AGENTS.md step 3). Be chatty: what came in, what you merged and why, what
you were unsure about, where sources differ. Questions for the user go in
as "- [ ] ..."; when answered, tick them: "- [x] ... -> answer". -->
""",
}

ITEM_LOG_HEAD = """# Conversion log: {key}

<!-- Chronological: append at the bottom. Agents: record everything a later
reader should know about how these pages were made: tools, page order,
blurry or ambiguous spots (name the page: p0003), what you guessed and why.
Every [unclear: ...] or [illegible] in a page needs a line here.
Questions for the user go in as "- [ ] ..."; when answered, tick them:
"- [x] ... -> answer". -->
"""

# ---------------------------------------------------------------- helpers

def norm(s):
    """Normalise text for quote matching: NFKC (splits ligatures), curly quotes,
    case, and drop all whitespace and hyphens -- so line wrapping, words
    hyphenated across lines, and dot-product vs dot product all match."""
    s = unicodedata.normalize("NFKC", s).translate(PUNCT).lower()
    return re.sub(r"[\s-]+", "", s)

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def combined_sha(hashes):
    return hashes[0] if len(hashes) == 1 else \
        hashlib.sha256("".join(hashes).encode()).hexdigest()

def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def taken(k, own=()):
    """Is key k used by an item, or by a raw/ file other than `own` (the
    originals being re-added from raw/)?"""
    clash = [f for f in (*RAW.glob(f"{k}.*"), *RAW.glob(f"{k}-p[0-9]*"))
             if f.resolve() not in own]
    return (ITEMS / k).exists() or bool(clash)

def free_key(k, srcs=()):
    own = {p.resolve() for p in srcs if under(p, RAW)}
    base, n = k, 2
    while taken(k, own):
        k, n = f"{base}-{n}", n + 1
    return k

def yval(v):
    if v is None or v == "":
        return ""
    if isinstance(v, int):
        return str(v)
    import yaml
    v = str(v)
    try:  # bare if it reads back unchanged, else quote (a JSON string is valid YAML)
        if "\n" not in v and yaml.safe_load(v) == v:
            return v
    except Exception:
        pass
    return json.dumps(v, ensure_ascii=False)

def frontmatter(fm):
    lines = [f"{f}: {yval(fm.get(f))}".rstrip() for f in PAGE_FIELDS]
    lines += [f"{f}: {yval(v)}".rstrip() for f, v in fm.items() if f not in PAGE_FIELDS]
    return "---\n" + "\n".join(lines) + "\n---\n"

def split_page(text):
    """Return (frontmatter dict, body) of a markdown file."""
    import yaml
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return yaml.safe_load(text[4:end]) or {}, text[end + 5:]
    return {}, text

def read_page(p):
    return split_page(p.read_text(encoding=UTF))

def write_page(p, fm, body):
    p.write_text(frontmatter(fm) + body, encoding=UTF)

def load_meta(d):
    import yaml
    return yaml.safe_load((d / "item.yaml").read_text(encoding=UTF)) or {}

def save_meta(d, m):
    import yaml
    (d / "item.yaml").write_text(yaml.safe_dump(m, sort_keys=False, allow_unicode=True),
                                 encoding=UTF)

def load_manifest():
    import yaml
    p = RAW / "manifest.yaml"
    if not p.is_file():
        return {}
    return (yaml.safe_load(p.read_text(encoding=UTF)) or {}).get("files") or {}

def save_manifest(files):
    import yaml
    RAW.mkdir(exist_ok=True)
    tmp = RAW / ".manifest.tmp"
    tmp.write_text(MANIFEST_HEAD + yaml.safe_dump({"files": files}, allow_unicode=True),
                   encoding=UTF)
    tmp.replace(RAW / "manifest.yaml")

def items():
    """Real items. Folders starting with _ (examples) or . (scratch) are skipped."""
    if not ITEMS.is_dir():
        return []
    return sorted(d for d in ITEMS.iterdir() if d.is_dir() and d.name[0] not in "_.")

def item_dir(key):
    d = ITEMS / key
    if not (d / "item.yaml").is_file():
        sys.exit(f"error: no item '{key}' (see catalog.md or `shelf.py doctor`)")
    return d

def page_path(key, n):
    return ITEMS / key / "pages" / f"{int(n):04d}.md"

def under(p, root):
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def origin(p):
    """Where an original came from, as recorded in the manifest."""
    for root, name in ((INBOX, "inbox"), (RAW, "raw")):
        if under(p, root):
            return f"{name}/" + p.resolve().relative_to(root.resolve()).as_posix()
    return str(p.resolve())

def collection_of(p):
    """The inbox folder a file was dropped into ("Sem 3/tutorials"), if any."""
    if not under(p, INBOX):
        return None
    rel = p.resolve().parent.relative_to(INBOX.resolve()).as_posix()
    return None if rel == "." else rel

def page_labels(reader):
    """Printed page numbers, if the PDF declares them (/PageLabels)."""
    try:
        if "/PageLabels" not in reader.trailer["/Root"]:
            return None
        return [int(l) if l.isdigit() else l for l in reader.page_labels]
    except Exception:
        return None

def cite(key, n, fm):
    pp = fm.get("printed_page")
    c = f"[{key} · p.{pp}]" if pp not in (None, "") else f"[{key} · pdf p.{n}]"
    return f"{c}  items/{key}/pages/{n:04d}.md"

def new_meta(k, title, raw, sha, source, status, pages, page_offset, collection, meta):
    m = {"key": k, "title": meta.get("title") or title,
         "authors": meta.get("authors") or [], "type": meta.get("type") or "misc",
         "year": meta.get("year"), "collection": collection, "source": source,
         "raw": raw, "sha256": sha, "status": status, "pages_pdf": pages,
         "page_offset": page_offset, "added": date.today().isoformat(),
         "tags": meta.get("tags") or []}
    if meta.get("doi"):
        m["doi"] = meta["doi"]
    return m

def plan_raw(srcs, names, force):
    """Decide each original's raw/ name; refuse files already in the library.
    Returns [(src, raw name, sha256)]."""
    known = {e.get("sha256"): (n, e.get("item")) for n, e in load_manifest().items()}
    out = []
    for src, name in zip(srcs, names):
        h = sha256(src)
        hit = known.get(h)
        alive = bool(hit and hit[1] and (ITEMS / hit[1]).is_dir())
        if under(src, RAW):  # re-adding an original that is already in raw/
            if alive and not force:
                sys.exit(f"error: raw/{src.name} already belongs to item {hit[1]}")
            out.append((src, src.name, h))
            continue
        if hit and not force:
            sys.exit(f"error: {origin(src)} is already in the library as raw/{hit[0]}"
                     + (f" (item {hit[1]})" if alive else "")
                     + ". It is a duplicate: ask the user before deleting it.")
        if (RAW / name).exists():
            sys.exit(f"error: raw/{name} already exists")
        out.append((src, name, h))
    return out

def build(k, fill, placed):
    """Build the item in items/.tmp-<key>, copy originals into raw/, and only
    when everything succeeded move the item into place and delete the inbox
    copies. A crash at any point leaves inbox/ untouched."""
    ITEMS.mkdir(exist_ok=True)
    RAW.mkdir(exist_ok=True)
    tmp = ITEMS / f".tmp-{k}"
    shutil.rmtree(tmp, ignore_errors=True)
    before = load_manifest()
    copied = []
    try:
        (tmp / "pages").mkdir(parents=True)
        (tmp / "figures").mkdir()
        fill(tmp)
        man = dict(before)
        for src, name, h in placed:
            dst = RAW / name
            if src.resolve() != dst.resolve():
                shutil.copy2(src, dst)
                copied.append(dst)
                if sha256(dst) != h:
                    raise OSError(f"copy of {src} into raw/ is corrupt")
            old = man.get(name) or {}
            man[name] = {"from": old.get("from") or origin(src), "sha256": h,
                         "added": old.get("added") or date.today().isoformat(),
                         "item": k}
        save_manifest(man)
        tmp.rename(ITEMS / k)
    except BaseException:
        for c in copied:
            c.unlink(missing_ok=True)
        if before or (RAW / "manifest.yaml").exists():
            save_manifest(before)
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    for src, name, _ in placed:  # originals are safe in raw/ now
        if under(src, INBOX):
            src.unlink()

def toc_stub(title):
    return (f"# {title}\n\n{TODO} fill in: section -> pdf page ranges\n\n"
            "| Section | pdf pages |\n|---|---|\n")

def log_append(d, what, lines):
    """Append a dated entry to items/<key>/log.md (creating it if needed)."""
    p = d / "log.md"
    head = "" if p.exists() else ITEM_LOG_HEAD.format(key=d.name.removeprefix(".tmp-"))
    with open(p, "a", encoding=UTF) as f:
        f.write(head + f"\n## {date.today().isoformat()} · {what}\n"
                + "".join(f"- {l}\n" for l in lines))

def origins(placed):
    return [f"raw/{name} (from {origin(src)})" for src, name, _ in placed]

def open_questions(p):
    """Unticked '- [ ] ...' lines in a log file."""
    if not p.is_file():
        return []
    return [l.strip()[6:] for l in p.read_text(encoding=UTF).splitlines()
            if l.strip().startswith("- [ ] ")]

# ---------------------------------------------------------------- commands

def init():
    for p in (INBOX, RAW, ITEMS, MAPPED):
        p.mkdir(exist_ok=True)
    for name, text in MAPPED_TEMPLATES.items():
        if not (MAPPED / name).exists():
            (MAPPED / name).write_text(text, encoding=UTF)
    print("shelf structure ready in", ROOT)

def add(pdf_path, key=None, source="original", page_offset=None, force=False,
        converted_from=None, **meta):
    from pypdf import PdfReader
    pdf = Path(pdf_path)
    if not pdf.is_file():
        sys.exit(f"error: no such file: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        sys.exit(f"error: {pdf.name} is not a PDF. For page images use `shelf.py "
                 "add-scan`; convert office files to PDF first (see AGENTS.md).")
    conv = Path(converted_from) if converted_from else None
    if conv and not conv.is_file():
        sys.exit(f"error: no such file: {conv}")
    try:
        reader = PdfReader(str(pdf))
    except Exception as e:
        sys.exit(f"error: cannot read PDF: {e}")
    if reader.is_encrypted and reader.decrypt("") == 0:
        sys.exit("error: password-protected PDF (unlock it first).")

    # str(): pypdf returns its own str subclass, which yaml refuses to dump
    title = str((reader.metadata.title if reader.metadata else "") or pdf.stem)
    k = free_key(slug(key) if key else
                 "doc-" + ("-".join(slug(title).split("-")[:4]) or "untitled"),
                 [pdf] + ([conv] if conv else []))
    srcs, names = [pdf], [f"{k}.pdf"]
    if conv:
        srcs.append(conv)
        names.append(f"{k}{conv.suffix.lower()}")
    placed = plan_raw(srcs, names, force)
    scanned = source != "original"
    labels = page_labels(reader)
    failed, todo = [], []

    def fill(d):
        for i, page in enumerate(reader.pages, 1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
                failed.append(i)
            figs = []
            try:  # best effort; needs pypdf >= 3.16
                for j, img in enumerate(page.images, 1):
                    name = f"p{i:04d}-{j}{Path(img.name).suffix or '.png'}"
                    (d / "figures" / name).write_bytes(img.data)
                    figs.append(name)
            except Exception:
                pass
            if labels:
                printed = labels[i - 1]
            elif page_offset is not None and i - page_offset >= 1:
                printed = i - page_offset
            else:
                printed = None
            body = text.rstrip() + "\n"
            if scanned:  # pypdf text of a scan is empty or junk
                body += f"\n{TODO} OCR or transcribe this page (`shelf.py render {k}`)\n"
                todo.append(i)
            body += "".join(f"\n![fig {Path(f).stem}](../figures/{f})\n" for f in figs)
            write_page(d / "pages" / f"{i:04d}.md",
                       {"key": k, "pdf_page": i, "printed_page": printed,
                        "section": None, "source": "pypdf"}, body)
        status = "pending" if todo else "partial" if failed else "extracted"
        m = new_meta(k, title, [f"raw/{placed[0][1]}"], placed[0][2], source, status,
                     len(reader.pages), page_offset, collection_of(pdf), meta)
        if conv:
            m["converted_from"] = f"raw/{placed[1][1]}"
        if failed:
            m["extract_failures"] = failed
        save_meta(d, m)
        (d / "toc.md").write_text(toc_stub(m["title"]), encoding=UTF)
        log_append(d, "shelf.py add", origins(placed) + [
            f"{len(reader.pages)} pages, text by pypdf"
            + ("; printed page numbers from the PDF's page labels" if labels else
               f"; printed page = pdf page - {page_offset}" if page_offset is not None else
               "; printed page numbers unknown"),
            *([f"pypdf failed on pages {failed}"] if failed else []),
            *([f"source: {source}, so every page is marked for OCR/transcription"]
              if scanned else [])])

    build(k, fill, placed)
    print(f"created items/{k} ({len(reader.pages)} pages"
          + (f", {len(failed)} failed: {failed}" if failed else "")
          + (", printed page numbers from PDF labels" if labels else "")
          + (", status pending: needs OCR/transcription" if todo else "") + ")")
    print(f"original: raw/{placed[0][1]}" + (" (moved out of inbox/)" if under(pdf, INBOX) else ""))
    if not key:
        print(f"note: '{k}' is a provisional key; rename with `shelf.py rekey {k} <key>`")
    return k

def add_scan(images, key, source="scan", force=False, **meta):
    imgs = [Path(p) for p in images]
    for p in imgs:
        if not p.is_file():
            sys.exit(f"error: no such file: {p}")
        if p.suffix.lower() not in IMG_EXT:
            sys.exit(f"error: {p.name}: not an image ({', '.join(sorted(IMG_EXT))})")
    if len(set(p.resolve() for p in imgs)) != len(imgs):
        sys.exit("error: the same image is listed twice")
    k = free_key(slug(key), imgs)
    placed = plan_raw(imgs, [f"{k}-p{i:03d}{p.suffix.lower()}"
                             for i, p in enumerate(imgs, 1)], force)

    def fill(d):
        for i, (_, name, _) in enumerate(placed, 1):
            write_page(d / "pages" / f"{i:04d}.md",
                       {"key": k, "pdf_page": i, "printed_page": None,
                        "section": None, "source": "agent-transcription"},
                       f"![page {i}](../../../raw/{name})\n\n{TODO} transcribe this page\n")
        meta.setdefault("type", "notes")
        m = new_meta(k, k, [f"raw/{n}" for _, n, _ in placed],
                     combined_sha([h for _, _, h in placed]), source, "pending",
                     len(placed), None, collection_of(imgs[0]), meta)
        save_meta(d, m)
        (d / "toc.md").write_text(toc_stub(m["title"]), encoding=UTF)
        log_append(d, "shelf.py add-scan", origins(placed) + [
            f"{len(placed)} page images, in the order given; every page needs transcription"])

    build(k, fill, placed)
    print(f"created items/{k} ({len(imgs)} page images, status: pending)")
    print(f"originals: raw/{placed[0][1]} ... raw/{placed[-1][1]}")
    return k

def render(key, dpi=150):
    """Page images of a scanned PDF into figures/pNNNN-full.png, linked at the
    top of each page file, so the agent can read and transcribe them."""
    d = item_dir(key)
    pdfs = [ROOT / r for r in load_meta(d).get("raw") or [] if r.endswith(".pdf")]
    if not pdfs or not pdfs[0].is_file():
        sys.exit(f"error: no raw PDF for '{key}'")
    if not shutil.which("pdftoppm"):
        sys.exit("error: render needs pdftoppm (poppler-utils / `conda install poppler`)")
    out = d / "figures" / ".render"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    try:
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdfs[0]), str(out / "p")],
                       check=True)
        n = 0
        for f in sorted(out.glob("p-*.png")):
            i = int(f.stem.rsplit("-", 1)[1])
            name = f"p{i:04d}-full.png"
            f.replace(d / "figures" / name)
            p = page_path(key, i)
            fm, body = read_page(p)
            link = f"![page {i}](../figures/{name})"
            if link not in body:
                write_page(p, fm, link + "\n\n" + body.lstrip("\n"))
            n += 1
    finally:
        shutil.rmtree(out, ignore_errors=True)
    log_append(d, "shelf.py render", [f"{n} page images at {dpi} dpi -> figures/pNNNN-full.png"])
    print(f"rendered {n} pages to items/{key}/figures/pNNNN-full.png")

def import_mineru(key, content_list):
    """Distribute MinerU's *_content_list.json over pages/NNNN.md by page_idx."""
    d = item_dir(key)
    src = Path(content_list)
    try:
        blocks = json.loads(src.read_text(encoding=UTF))
    except Exception as e:
        sys.exit(f"error: cannot read {src}: {e}")
    m = load_meta(d)
    n_pages = m.get("pages_pdf") or 0
    bodies = {i: [] for i in range(1, n_pages + 1)}
    figs = Counter()
    for f in (d / "figures").glob("p*-*"):
        if not f.stem.endswith("-full"):
            f.unlink()  # old pypdf figures; MinerU's replace them
    for b in blocks:
        i = int(b.get("page_idx", 0)) + 1
        if i not in bodies:
            sys.exit(f"error: block on pdf page {i}, but item has {n_pages} pages")
        t = b.get("type")
        if t == "text":
            lvl = int(b.get("text_level") or 0)
            bodies[i].append(("#" * lvl + " " if lvl else "") + b.get("text", "").strip())
        elif t == "equation":
            bodies[i].append(b.get("text", "").strip())
        elif t in ("image", "table"):
            parts = []
            if t == "table" and b.get("table_body"):
                parts.append(b["table_body"].strip())
            img = b.get("img_path")
            if img and (src.parent / img).is_file():
                figs[i] += 1
                name = f"p{i:04d}-{figs[i]}{Path(img).suffix or '.png'}"
                shutil.copy2(src.parent / img, d / "figures" / name)
                parts.append(f"![fig {Path(name).stem}](../figures/{name})")
            cap = b.get(f"{t}_caption") or b.get("img_caption") or []
            parts += [c.strip() for c in cap if c.strip()]
            bodies[i].append("\n\n".join(parts))
    for i, parts in bodies.items():
        p = page_path(key, i)
        fm = read_page(p)[0] if p.exists() else {"key": key, "pdf_page": i}
        fm["source"] = "mineru"
        old = read_page(p)[1] if p.exists() else ""
        full = re.search(r"!\[page \d+\]\(\.\./figures/p\d{4}-full\.png\)", old)
        write_page(p, fm, (full.group(0) + "\n\n" if full else "")
                   + "\n\n".join(x for x in parts if x) + "\n")
    if m.get("status") in ("pending", "partial"):
        m["status"] = "extracted"
        save_meta(d, m)
    empty = [i for i, parts in bodies.items() if not parts]
    log_append(d, "shelf.py import-mineru", [
        f"{len(blocks)} MinerU blocks from {src.name} -> all {n_pages} pages "
        f"(replacing earlier text); {sum(figs.values())} figures",
        *([f"MinerU found nothing on pages {empty}: check them"] if empty else [])])
    print(f"imported {len(blocks)} MinerU blocks into {n_pages} pages "
          f"({sum(figs.values())} figures)")

def parse_toc(text):
    """Rows of the `| Section | pdf pages |` table -> [(section, first, last)]."""
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        r = re.fullmatch(r"(\d+)\s*(?:[-–—]|to)?\s*(\d+)?", cells[-1])
        if r and cells[0]:
            a = int(r.group(1))
            rows.append((cells[0], a, int(r.group(2) or a)))
    return rows

def apply_toc(key):
    """Each page gets the narrowest toc section covering it, so a subsection
    beats its chapter; ties go to the section that started first."""
    d = item_dir(key)
    rows = parse_toc((d / "toc.md").read_text(encoding=UTF))
    if not rows:
        sys.exit("error: toc.md has no `| Section | a-b |` rows yet")
    n = 0
    for p in sorted((d / "pages").glob("*.md")):
        i = int(p.stem)
        hits = [r for r in rows if r[1] <= i <= r[2]]
        if not hits:
            continue
        sec = min(hits, key=lambda r: (r[2] - r[1], r[1]))[0]
        fm, body = read_page(p)
        if fm.get("section") != sec:
            fm["section"] = sec
            write_page(p, fm, body)
            n += 1
    print(f"updated section on {n} pages from {len(rows)} toc rows")

def rekey(old, new):
    """Rename an item and every reference to it: pages, manifest, mapped/.
    Files in raw/ keep their names (originals are never renamed)."""
    src = item_dir(old)
    new = slug(new)
    if not KEY_RE.match(new):
        sys.exit("error: key must be lowercase letters/digits joined by '-'")
    if (ITEMS / new).exists():
        sys.exit(f"error: items/{new} already exists")
    for p in sorted((src / "pages").glob("*.md")):
        fm, body = read_page(p)
        fm["key"] = new
        write_page(p, fm, body)
    m = load_meta(src)
    m["key"] = new
    save_meta(src, m)
    man = load_manifest()
    for e in man.values():
        if e.get("item") == old:
            e["item"] = new
    if man:
        save_manifest(man)
    src.rename(ITEMS / new)
    log_append(ITEMS / new, "shelf.py rekey", [f"renamed from {old} (raw/ file names unchanged)"])
    token = re.compile(rf"(?<![a-z0-9-]){re.escape(old)}(?![a-z0-9-])")
    n = 0
    for f in sorted(MAPPED.rglob("*.md")) if MAPPED.is_dir() else []:
        t = f.read_text(encoding=UTF)
        t2 = token.sub(new, t)
        if t2 != t:
            f.write_text(t2, encoding=UTF)
            n += 1
    print(f"renamed items/{old} -> items/{new}" + (f"; updated {n} mapped/ file(s)" if n else ""))

def verify(key, n, quote, mark=False):
    p = page_path(key, n)
    q = norm(quote)
    ok = bool(q) and p.exists() and q in norm(read_page(p)[1])
    if not ok:
        print("NOT FOUND on that page")
        others = [int(x.stem) for x in sorted((ITEMS / key / "pages").glob("*.md"))
                  if q and q in norm(read_page(x)[1])]
        if others:
            print(f"  ...but found on pdf page(s) {others}: check your page alignment")
        sys.exit(1)
    print("OK")
    if mark:
        d = item_dir(key)
        errs, _ = lint_item(d, status="verified")
        if errs:
            print("not marked verified yet:")
            for e in errs:
                print("  -", e)
            sys.exit(1)
        m = load_meta(d)
        m["status"] = "verified"
        save_meta(d, m)
        print(f"items/{key} status: verified")
    sys.exit(0)

def find(quote, key=None):
    q = norm(quote)
    if len(q) < 3:
        sys.exit("error: quote too short")
    hits = 0
    for d in [item_dir(key)] if key else items():
        for p in sorted((d / "pages").glob("*.md")):
            fm, body = read_page(p)
            if q in norm(body):
                print(cite(d.name, int(p.stem), fm))
                hits += 1
    if not hits:
        print("not found (a quote that crosses a page break won't match; try a shorter one)")
        sys.exit(1)

def lint_item(d, status=None, check_hash=True):
    """Return (errors, warnings) for one item. `status` overrides item.yaml's,
    to ask "could this item be marked X?"."""
    err, warn = [], []
    try:
        m = load_meta(d)
    except FileNotFoundError:
        return ["missing item.yaml"], []
    except Exception as e:
        return [f"item.yaml unreadable: {e}"], []
    st = status or m.get("status")
    done = st in ("extracted", "verified")
    for f in REQUIRED:
        if f not in m:
            err.append(f"item.yaml: missing field '{f}'")
    if m.get("key") != d.name:
        err.append(f"item.yaml key '{m.get('key')}' != folder name '{d.name}'")
    if not KEY_RE.match(d.name):
        err.append("key must be lowercase letters/digits joined by '-'")
    for f, ok in (("status", STATUSES), ("type", TYPES), ("source", SOURCES)):
        if f in m and m[f] not in ok:
            err.append(f"item.yaml: {f} '{m[f]}' not one of {', '.join(ok)}")
    for f in ("authors", "tags", "raw"):
        if not isinstance(m.get(f, []), list):
            err.append(f"item.yaml: {f} must be a list")
    if m.get("page_offset") is not None and not isinstance(m.get("page_offset"), int):
        err.append("item.yaml: page_offset must be an integer or empty")
    if m.get("type") not in ("notes", "exam", "tutorial", "assignment", "cheatsheet") \
            and (not m.get("authors") or not m.get("year")):
        (err if st == "verified" else warn).append("item.yaml: authors/year not filled in")

    if (d / "source.pdf").exists():
        err.append("source.pdf inside the item is the spec 1.x layout; originals live in raw/")
    raws = m.get("raw") if isinstance(m.get("raw"), list) else []
    if "raw" in m and not raws:
        err.append("item.yaml: raw is empty (which originals is this built from?)")
    missing = [r for r in raws if not (ROOT / r).is_file()]
    if missing:
        warn.append(f"raw file(s) not present: {', '.join(missing)} (fine in a copy without raw/)")
    elif raws and check_hash:
        if combined_sha([sha256(ROOT / r) for r in raws]) != m.get("sha256"):
            err.append("sha256 does not match the raw file(s) (were they modified?)")

    pages = sorted((d / "pages").glob("*.md"))
    if [p.name for p in pages] != [f"{i:04d}.md" for i in range(1, len(pages) + 1)]:
        err.append("pages/ must be 0001.md ... NNNN.md with no gaps")
    if m.get("pages_pdf") != len(pages):
        err.append(f"pages_pdf is {m.get('pages_pdf')} but pages/ has {len(pages)} files")
    todo = empty = raw_links = 0
    unclear = []  # pages with [unclear: ...] / [illegible]
    for p in pages:
        try:
            fm, body = read_page(p)
        except Exception as e:
            err.append(f"{p.name}: bad frontmatter: {e}")
            continue
        if not fm:
            err.append(f"{p.name}: missing frontmatter")
            continue
        if fm.get("key") != d.name:
            err.append(f"{p.name}: key '{fm.get('key')}' != '{d.name}'")
        if fm.get("pdf_page") != int(p.stem):
            err.append(f"{p.name}: pdf_page is {fm.get('pdf_page')}")
        if not fm.get("source"):  # free text: whatever tool produced the page
            err.append(f"{p.name}: source is empty (name the tool that extracted it)")
        for ref in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", body):
            target = p.parent / ref
            if "://" in ref or target.exists():
                continue
            if under(target, RAW):
                raw_links += 1
            else:
                err.append(f"{p.name}: broken image link {ref}")
        todo += TODO in body
        empty += not re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body).strip()
        if UNCLEAR_RE.search(body):
            unclear.append(p.stem)
    if todo and done:
        err.append(f"{todo} page(s) still contain {TODO} but status is {st}")
    if empty:
        warn.append(f"{empty} page(s) have no text (blank, figure-only, or needs OCR?)")
    if raw_links and not missing:
        err.append(f"{raw_links} image link(s) into raw/ point to missing files")

    log = d / "log.md"
    if not log.exists():
        err.append("log.md missing (the conversion log, SPEC.md §4.5)")
    else:
        text = log.read_text(encoding=UTF)
        silent = [f"p{s}" for s in unclear if f"p{s}" not in text]
        if silent:
            warn.append(f"unclear/illegible text on {', '.join(silent)} "
                        "but log.md doesn't explain it")
        if open_questions(log):
            warn.append(f"{len(open_questions(log))} open question(s) for the user in log.md")

    toc = d / "toc.md"
    if not toc.exists():
        err.append("toc.md missing")
    elif TODO in toc.read_text(encoding=UTF):
        (err if st == "verified" else warn).append("toc.md not filled in yet")
    return err, warn

def lint_mapped():
    """Check mapped/: frontmatter, sources, and that every citation resolves
    to a real page in items/. Returns [(path, errors, warnings)]."""
    if not MAPPED.is_dir():
        return []
    keys = {d.name for d in items()}
    printed = {}

    def printed_pages(key):
        if key not in printed:
            printed[key] = {str(read_page(p)[0].get("printed_page"))
                            for p in (ITEMS / key / "pages").glob("*.md")}
        return printed[key]

    out = []
    for f in sorted(MAPPED.rglob("*.md")):
        err, warn = [], []
        text = f.read_text(encoding=UTF)
        try:
            fm, body = split_page(text)
        except Exception as e:
            out.append((f, [f"bad frontmatter: {e}"], []))
            continue
        entry = f.parent == MAPPED and f.name in ENTRY_FILES
        if TODO in text:
            warn.append("not filled in yet (contains shelf:todo)")
        srcs = fm.get("sources") or []
        if not entry:
            if not fm.get("title") or not isinstance(fm.get("sources"), list):
                err.append("frontmatter needs title: and sources: [item keys]")
            err += [f"sources: no item '{s}'" for s in srcs if s not in keys]
        # citations inside `code` or ``` blocks are examples, not claims
        prose = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", body, flags=re.S))
        cites = CITE_RE.findall(prose)
        for key, pdf, n in cites:
            first = re.split(r"[–-]", n)[0]
            if key not in keys:
                err.append(f"[{key} · ...] cites an item that does not exist")
            elif not (page_path(key, first).exists() if pdf and first.isdigit()
                      else not pdf and first in printed_pages(key)):
                err.append(f"[{key} · {pdf}p.{n}] does not resolve to a page")
            elif not entry and key not in srcs:
                warn.append(f"cites {key}, which is missing from sources:")
        if not entry and not cites:
            warn.append("no citations")
        out.append((f, list(dict.fromkeys(err)), list(dict.fromkeys(warn))))
    return out

def lint(keys=None):
    dirs = [item_dir(k) for k in keys] if keys else items()
    results = [(f"items/{d.name}", *lint_item(d)) for d in dirs]
    if not keys:
        results += [(f.relative_to(ROOT).as_posix(), e, w) for f, e, w in lint_mapped()]
    bad = 0
    for name, errs, warns in results:
        print(("FAIL " if errs else "ok   ") + name)
        for e in errs:
            print("  error:", e)
        for w in warns:
            print("  warn: ", w)
        bad += bool(errs)
    for t in ITEMS.glob(".tmp-*"):
        print(f"warn: leftover {t.name} from an interrupted add (safe to delete)")
    print(f"{len(results)} checked, {bad} with errors")
    sys.exit(1 if bad else 0)

def catalog():
    rows = []
    for d in items():
        try:
            m = load_meta(d)
        except Exception as e:
            rows.append(f"| ~~{d.name}~~ | unreadable item.yaml: {e} | | | | | | |")
            continue
        au = m.get("authors") or []
        au = "; ".join(au[:2]) + (" et al." if len(au) > 2 else "")
        cells = [f"`{d.name}`", m.get("title"), au, m.get("year"), m.get("type"),
                 m.get("collection"), m.get("pages_pdf"), m.get("status")]
        rows.append("| " + " | ".join(str(c if c is not None else "").replace("|", "\\|")
                                      for c in cells) + " |")
    out = ["# Catalog", "", "<!-- generated by `python3 shelf.py catalog`; do not edit -->", ""]
    if rows:
        out += ["| key | title | authors | year | type | collection | pages | status |",
                "|---|---|---|---|---|---|---|---|", *rows]
    else:
        out += ["No items yet. Drop files into `inbox/` and tell your agent:",
                '"Process inbox/ per AGENTS.md."']
    (ROOT / "catalog.md").write_text("\n".join(out) + "\n", encoding=UTF)
    print(f"catalog.md updated ({len(rows)} items)")

def bib(keys=None):
    for d in [item_dir(k) for k in keys] if keys else items():
        m = load_meta(d)
        f = {"title": m.get("title"), "author": " and ".join(m.get("authors") or []),
             "year": m.get("year"), **{x: m.get(x) for x in BIB_FIELDS}}
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in f.items() if v not in (None, ""))
        print(f"@{BIBTYPE.get(m.get('type'), 'misc')}{{{d.name},\n{body}\n}}\n")

def doctor():
    import importlib.util
    print(f"shelf.py {__version__} (spec {SPEC_VERSION}), python {sys.version.split()[0]}")
    for mod in ("pypdf", "yaml"):
        print(f"  {'OK     ' if importlib.util.find_spec(mod) else 'MISSING'} python:{mod}")
    for cmd, why in (("mineru", "best extraction for math/tables/figures"),
                     ("marker_single", "alternative extractor"),
                     ("pdftoppm", "`render`: page images of scanned PDFs"),
                     ("tesseract", "basic OCR"),
                     ("soffice", "convert .docx/.pptx to PDF")):
        print(f"  {'OK     ' if shutil.which(cmd) else '--     '} cli:{cmd} ({why})")
    print("  + any tools the user lists in ~/TOOLS.md")

    man = load_manifest()
    known = {e.get("sha256"): n for n, e in man.items()}
    files = sorted(f for f in INBOX.rglob("*") if f.is_file()
                   and not any(part[0] == "." for part in f.relative_to(INBOX).parts)) \
        if INBOX.is_dir() else []
    print(f"\ninbox/: {len(files)} file(s) waiting")
    for f in files:
        ext, dup = f.suffix.lower(), known.get(sha256(f))
        kind = (f"DUPLICATE of raw/{dup}" if dup else
                "pdf" if ext == ".pdf" else
                "image" if ext in IMG_EXT else
                "office file: convert to PDF, then add --converted-from" if ext in OFFICE_EXT
                else "unsupported type: ask the user")
        print(f"  {f.relative_to(INBOX).as_posix()}: {kind}")

    rawfiles = sorted(f.name for f in RAW.iterdir() if f.is_file() and f.name[0] != "."
                      and f.name != "manifest.yaml") if RAW.is_dir() else []
    orphans = [n for n in rawfiles if n in man and not (ITEMS / str(man[n].get("item"))).is_dir()]
    unknown = [n for n in rawfiles if n not in man]
    print(f"raw/: {len(rawfiles)} original(s)")
    for n in orphans:
        print(f"  {n}: no item (add it again from raw/)")
    for n in unknown:
        print(f"  {n}: not in manifest.yaml (add it from raw/ to record it)")

    its, counts = items(), Counter()
    for d in its:
        try:
            counts[load_meta(d).get("status")] += 1
        except Exception:
            counts["unreadable"] += 1
    print(f"items/: {len(its)} item(s)" + (" (" + ", ".join(
        f"{v} {k}" for k, v in sorted(counts.items(), key=str)) + ")" if its else ""))
    for d in its:
        try:
            if load_meta(d).get("status") in ("pending", "partial"):
                print(f"  {d.name}: {load_meta(d).get('status')}")
        except Exception:
            pass

    mfiles = sorted(MAPPED.rglob("*.md")) if MAPPED.is_dir() else []
    print(f"mapped/: {len(mfiles)} file(s)")
    subj = MAPPED / "subject.md"
    if not subj.exists() or TODO in subj.read_text(encoding=UTF):
        print("  subject.md not filled in: interview the user first (AGENTS.md step 1)")

    qs = [(f"items/{d.name}/log.md", q) for d in its for q in open_questions(d / "log.md")]
    qs += [("mapped/log.md", q) for q in open_questions(MAPPED / "log.md")]
    if qs:
        print(f"\nopen questions for the user: {len(qs)} (ask them, then tick '- [x] ... -> answer')")
        for where, q in qs[:15]:
            print(f"  {where}: {q}")
        if len(qs) > 15:
            print(f"  ... and {len(qs) - 15} more")
    if its or mfiles:
        print("run `python3 shelf.py lint` for details")

# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(prog="shelf.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version",
                    version=f"shelf.py {__version__} (spec {SPEC_VERSION})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def meta_args(p, default_type):
        p.add_argument("--title")
        p.add_argument("--author", action="append", dest="authors", metavar="'SURNAME, GIVEN'",
                       help="repeat for each author, in order")
        p.add_argument("--year", type=int)
        p.add_argument("--type", choices=TYPES, default=default_type)
        p.add_argument("--tag", action="append", dest="tags")
        p.add_argument("--doi")
        p.add_argument("--force", action="store_true", help="add even if already present")

    sub.add_parser("init", help="create inbox/ raw/ items/ mapped/")
    sub.add_parser("doctor", help="deps, waiting inbox files, library health")
    p = sub.add_parser("add", help="add a PDF (moves it from inbox/ to raw/)")
    p.add_argument("pdf")
    p.add_argument("--key", help="e.g. vaswani2017-attention-is-all-you (see SPEC.md)")
    p.add_argument("--source", default="original", choices=SOURCES,
                   help="scan/photocopy: pages are marked for OCR/transcription")
    p.add_argument("--page-offset", type=int,
                   help="printed page = pdf page - N (only if the PDF has no page labels)")
    p.add_argument("--converted-from", metavar="FILE",
                   help="the .docx/.pptx this PDF was converted from (kept in raw/ too)")
    meta_args(p, "misc")
    p = sub.add_parser("add-scan", help="add page images as one document, in the order given")
    p.add_argument("images", nargs="+")
    p.add_argument("--key", required=True)
    p.add_argument("--source", default="scan", choices=SOURCES)
    meta_args(p, "notes")
    p = sub.add_parser("render", help="page images of a scanned PDF item (needs pdftoppm)")
    p.add_argument("key"); p.add_argument("--dpi", type=int, default=150)
    p = sub.add_parser("import-mineru", help="replace page text with MinerU output")
    p.add_argument("key"); p.add_argument("content_list", help="MinerU *_content_list.json")
    p = sub.add_parser("apply-toc", help="copy sections from toc.md into pages")
    p.add_argument("key")
    p = sub.add_parser("rekey", help="rename an item everywhere")
    p.add_argument("old"); p.add_argument("new")
    p = sub.add_parser("page", help="print path of pages/NNNN.md")
    p.add_argument("key"); p.add_argument("n", type=int)
    p = sub.add_parser("verify", help="check a quote is on a given pdf page")
    p.add_argument("key"); p.add_argument("pdf_page", type=int); p.add_argument("quote")
    p.add_argument("--mark", action="store_true",
                   help="on success, set status: verified (if the item passes lint)")
    p = sub.add_parser("find", help="list pages containing a quote, as citations")
    p.add_argument("quote"); p.add_argument("--key")
    p = sub.add_parser("lint", help="check items and mapped/ citations")
    p.add_argument("keys", nargs="*")
    sub.add_parser("catalog", help="regenerate catalog.md")
    p = sub.add_parser("bib", help="print BibTeX for items")
    p.add_argument("keys", nargs="*")

    a = ap.parse_args()
    meta = {f: getattr(a, f, None) for f in ("title", "authors", "year", "type", "tags", "doi")}
    if a.cmd == "init": init()
    elif a.cmd == "doctor": doctor()
    elif a.cmd == "add":
        add(a.pdf, key=a.key, source=a.source, page_offset=a.page_offset,
            force=a.force, converted_from=a.converted_from, **meta)
    elif a.cmd == "add-scan":
        add_scan(a.images, a.key, source=a.source, force=a.force, **meta)
    elif a.cmd == "render": render(a.key, a.dpi)
    elif a.cmd == "import-mineru": import_mineru(a.key, a.content_list)
    elif a.cmd == "apply-toc": apply_toc(a.key)
    elif a.cmd == "rekey": rekey(a.old, a.new)
    elif a.cmd == "page":
        p = page_path(a.key, a.n)
        if not p.exists():
            sys.exit(f"error: no page {a.n} for '{a.key}'")
        print(p.relative_to(ROOT).as_posix())
    elif a.cmd == "verify": verify(a.key, a.pdf_page, a.quote, mark=a.mark)
    elif a.cmd == "find": find(a.quote, key=a.key)
    elif a.cmd == "lint": lint(a.keys)
    elif a.cmd == "catalog": catalog()
    elif a.cmd == "bib": bib(a.keys)

if __name__ == "__main__":
    main()
