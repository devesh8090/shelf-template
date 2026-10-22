#!/usr/bin/env python3
"""shelf.py - reference implementation of the shelf library format (v0.5).

Four levels (SPEC.md); the agent workflow is in AGENTS.md.
  L1 inbox/   the human dumps anything here, in any folders
  L2 raw/     every original, flat, renamed, never modified (+ manifest.yaml)
  L3 items/   one folder per document, one markdown file per page: ground truth
  L4 mapped/  the agent's organised, de-duplicated notes, citing L3

Commands:
  init                              create the folders and mapped/ templates
  doctor                            deps, tools, waiting inbox files, library health
  setup [--dry-run] [--refresh]     pick + install one tool per job for this computer
  tools                             show this computer's tools (~/.shelf/tools.json)
  extract <key>                     fill an item's pages with this computer's local tool
  add <file.pdf> --key K [...]      L1->L2->L3 for a PDF (moves it out of inbox/)
  add-scan <img...> --key K [...]   same for page images, in the order given
  render <key>                      page images of a scanned PDF, for transcription
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

__version__ = "0.5.0"
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
HEIC_EXT = {".heic", ".heif"}  # iPhone photos: kept as originals, read via a JPG copy
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

def heic_converter():
    """A command that turns a HEIC photo into a JPG, from the `images` tools.
    ImageMagick only counts if this build can actually read HEIC."""
    if shutil.which("heif-convert"):
        return lambda src, dst: ["heif-convert", src, dst]
    if shutil.which("heif-dec"):
        return lambda src, dst: ["heif-dec", src, "-o", dst]
    if shutil.which("magick"):
        r = subprocess.run(["magick", "-list", "format"], capture_output=True, text=True)
        if re.search(r"^\s*HEIC\s+\S+\s+r", r.stdout, re.M):
            return lambda src, dst: ["magick", src, dst]
    return None

def add_scan(images, key, source="scan", force=False, **meta):
    imgs = [Path(p) for p in images]
    for p in imgs:
        if not p.is_file():
            sys.exit(f"error: no such file: {p}")
        if p.suffix.lower() not in IMG_EXT | HEIC_EXT:
            sys.exit(f"error: {p.name}: not an image "
                     f"({', '.join(sorted(IMG_EXT | HEIC_EXT))})")
    if len(set(p.resolve() for p in imgs)) != len(imgs):
        sys.exit("error: the same image is listed twice")
    heic = [p for p in imgs if p.suffix.lower() in HEIC_EXT]
    convert = heic_converter() if heic else None
    if heic and not convert:
        sys.exit("error: HEIC photos need imagemagick or heif-convert to be readable "
                 "(see `shelf.py tools`)")
    k = free_key(slug(key), imgs)
    placed = plan_raw(imgs, [f"{k}-p{i:03d}{p.suffix.lower()}"
                             for i, p in enumerate(imgs, 1)], force)

    def fill(d):
        for i, (src, name, _) in enumerate(placed, 1):
            link = f"../../../raw/{name}"
            if src.suffix.lower() in HEIC_EXT:  # original kept in raw/, readable copy here
                jpg = d / "figures" / f"p{i:04d}-full.jpg"
                r = subprocess.run(convert(str(src), str(jpg)), capture_output=True)
                if r.returncode or not jpg.is_file():
                    sys.exit(f"error: could not convert {src.name} to JPG: "
                             f"{r.stderr.decode(errors='replace').strip()[:200]}")
                link = f"../figures/{jpg.name}"
            write_page(d / "pages" / f"{i:04d}.md",
                       {"key": k, "pdf_page": i, "printed_page": None,
                        "section": None, "source": "agent-transcription"},
                       f"![page {i}]({link})\n\n{TODO} transcribe this page\n")
        meta.setdefault("type", "notes")
        m = new_meta(k, k, [f"raw/{n}" for _, n, _ in placed],
                     combined_sha([h for _, _, h in placed]), source, "pending",
                     len(placed), None, collection_of(imgs[0]), meta)
        save_meta(d, m)
        (d / "toc.md").write_text(toc_stub(m["title"]), encoding=UTF)
        log_append(d, "shelf.py add-scan", origins(placed) + [
            f"{len(placed)} page images, in the order given; every page needs transcription",
            *([f"{len(heic)} HEIC photo(s): originals in raw/, readable JPG copies "
               "in figures/pNNNN-full.jpg"] if heic else [])])

    build(k, fill, placed)
    print(f"created items/{k} ({len(imgs)} page images, status: pending)")
    print(f"originals: raw/{placed[0][1]} ... raw/{placed[-1][1]}")
    return k

def raw_pdf(d, key):
    pdfs = [ROOT / r for r in load_meta(d).get("raw") or [] if r.endswith(".pdf")]
    if not pdfs or not pdfs[0].is_file():
        sys.exit(f"error: no raw PDF for '{key}'")
    return pdfs[0]

def render_pages(pdf, out, dpi=150):
    """Render every page of `pdf` into `out`; returns {pdf page: png path}."""
    if shutil.which("pdftoppm"):
        cmd = ["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(out / "p")]
    elif shutil.which("mutool"):
        cmd = ["mutool", "draw", "-r", str(dpi), "-o", str(out / "p-%d.png"), str(pdf)]
    else:
        sys.exit("error: rendering pages needs pdftoppm or mutool (`shelf.py setup`)")
    subprocess.run(cmd, check=True)  # both write p-1.png / p-01.png ...
    return {int(f.stem.rsplit("-", 1)[1]): f for f in out.glob("p-*.png")}

def render(key, dpi=150):
    """Page images of a scanned PDF into figures/pNNNN-full.png, linked at the
    top of each page file, so the agent can read and transcribe them."""
    d = item_dir(key)
    pdf = raw_pdf(d, key)
    out = d / "figures" / ".render"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    try:
        n = 0
        for i, f in sorted(render_pages(pdf, out, dpi).items()):
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

# ---------------------------------------------------------------- tools

MANAGERS = {  # package manager -> (command that proves it exists, install template)
    "apt": ("apt-get", "sudo apt-get install -y {pkg}"),
    "dnf": ("dnf", "sudo dnf install -y {pkg}"),
    "pacman": ("pacman", "sudo pacman -S --noconfirm {pkg}"),
    "brew": ("brew", "brew install {pkg}"),
    "winget": ("winget", "winget install -e --accept-package-agreements --id {pkg}"),
    "conda": ("conda", "conda install -y -c conda-forge {pkg}"),
}
ADMIN_MANAGERS = ("apt", "dnf", "pacman")  # need sudo: batched into one command
MANAGER_ORDER = {"linux": ("apt", "dnf", "pacman", "brew", "conda", "pip"),
                 "darwin": ("brew", "conda", "pip"),
                 "win32": ("winget", "conda", "pip")}
TIERS = ("low", "mid", "high")
EXTRACTORS = ("mineru", "ocrmypdf", "tesseract", "pypdf")  # what `extract` can drive
REGISTRY_MAX_AGE = 183  # days before `tools` suggests refreshing the registry

def load_registry():
    p = ROOT / "tools.registry.json"
    if not p.is_file():
        sys.exit("error: tools.registry.json is missing (it ships with shelf)")
    return json.loads(p.read_text(encoding=UTF))

def platform_key():
    return ("win32" if sys.platform.startswith("win") else
            "darwin" if sys.platform == "darwin" else "linux")

def ram_gb():
    try:
        if sys.platform.startswith("linux"):
            kb = re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1)
            return int(kb) / 2**20
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True).stdout
            return int(out) / 2**30
        if sys.platform.startswith("win"):
            import ctypes

            class Mem(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                            *[(f, ctypes.c_ulonglong) for f in
                              ("total", "avail", "pf", "apf", "virt", "avirt", "ext")]]
            m = Mem()
            m.length = ctypes.sizeof(Mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.total / 2**30
    except Exception:
        pass
    return 0.0

def hardware():
    """RAM, GPU and the tier that decides which tools this machine can run:
    high = NVIDIA GPU with >= 6 GB, or Apple silicon with >= 16 GB;
    mid = >= 8 GB RAM; low = anything less."""
    import os, platform
    ram, gpu, vram = ram_gb(), None, 0.0
    if shutil.which("nvidia-smi"):
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True)
        if r.returncode == 0 and "," in r.stdout:
            name, mib = r.stdout.strip().splitlines()[0].rsplit(",", 1)
            gpu, vram = name.strip(), int(mib) / 1024
    apple = sys.platform == "darwin" and platform.machine() == "arm64"
    if apple and not gpu:
        gpu = "Apple silicon (shared memory)"
    tier = ("high" if vram >= 6 or (apple and ram >= 15.5) else
            "mid" if ram >= 7.5 else "low")  # a "8 GB" machine reports ~7.6
    return {"os": platform_key(), "ram_gb": round(ram, 1), "cpus": os.cpu_count(),
            "gpu": gpu, "vram_gb": round(vram, 1), "tier": tier}

def externally_managed():
    """True for a system python that refuses pip installs (PEP 668, e.g. Ubuntu)."""
    import sysconfig
    return (sys.prefix == sys.base_prefix and
            (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists())

def extra_bin_dirs():
    """Where user-level installs land when they aren't on PATH: pipx and
    `uv tool` (~/.local/bin), and the usual conda/miniforge base folders."""
    home = Path.home()
    return [home / ".local" / "bin",
            *[home / base / ("Scripts" if platform_key() == "win32" else "bin")
              for base in ("miniforge3", "miniconda3", "anaconda3")],
            Path("/opt/conda/bin")]

def find_exe(name):
    """Full path of a program: a path the user gave `setup --use`, else PATH,
    else the usual user-level install folders."""
    import os
    known = ((tools_json() or {}).get("paths") or {}).get(name)
    if known and Path(known).is_file():
        return known
    hit = shutil.which(name)
    if hit:
        return hit
    for d in extra_bin_dirs():
        for cand in (d / name, d / f"{name}.exe"):
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    return None

def installed(tool):
    return bool(tool.get("builtin") or any(find_exe(x) for x in tool.get("detect", [])))

def install_plan(tool):
    """(package manager, package) this machine would install the tool with."""
    for mgr in MANAGER_ORDER[platform_key()]:
        pkg = tool.get("install", {}).get(mgr)
        if pkg and (mgr == "pip" or shutil.which(MANAGERS[mgr][0])):
            return mgr, pkg
    return None

def choose(reg, tier, keep=None):
    """Deterministic pick per category: `use` is the first tool that fits the
    tier and can be had here (installed already, or installable); `backup` is
    the next fitting tool that is already installed. At most one install per
    category, so the set of tools converges instead of growing. `keep` holds
    earlier picks ({category: tool}), which stay as long as the registry
    still lists them."""
    rank, keep = TIERS.index(tier), keep or {}
    picks = {}
    for cat, c in reg["categories"].items():
        fits = [t for t in c["tools"] if TIERS.index(t.get("min_tier", "low")) <= rank]
        use = (next((t for t in c["tools"] if t["name"] == keep.get(cat)), None) or
               next((t for t in fits if installed(t) or install_plan(t)), None))
        backup = next((t for t in fits if t is not use and installed(t)), None)
        picks[cat] = (use, backup)
    return picks

def tools_path():
    """One tools.json per computer, shared by every shelf library on it."""
    import os
    return Path(os.environ.get("SHELF_HOME") or Path.home() / ".shelf") / "tools.json"

def tools_json():
    p = tools_path()
    try:
        return json.loads(p.read_text(encoding=UTF)) if p.is_file() else None
    except Exception:
        return None

def write_tools_json(hw, reg, picks, admin, paths=None):
    old = tools_json() or {}
    cats = {}
    for cat, (use, backup) in picks.items():
        c = reg["categories"][cat]
        cats[cat] = {"job": c["job"],
                     "use": use["name"] if use else None,
                     "backup": backup["name"] if backup else None,
                     "fallback": c.get("fallback"),
                     "ready": bool(use and installed(use)),
                     "path": next((find_exe(x) for x in (use or {}).get("detect", [])
                                   if find_exe(x)), None)}
    out = {"_about": "Written by `shelf.py setup` for this computer; every shelf "
                     "library on it uses these tools. Only `overrides` is yours to edit.",
           "checked": date.today().isoformat(), "machine": hw,
           "registry_updated": reg.get("updated"), "categories": cats,
           "pending_admin": admin or None,
           "paths": {**(old.get("paths") or {}), **(paths or {})},
           "overrides": old.get("overrides") or {}}
    tools_path().parent.mkdir(parents=True, exist_ok=True)
    tools_path().write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding=UTF)
    return out

def effective(t, cat):
    """The tool to use for a category right now: the user's override, else
    `use` if installed, else `backup`, else None (use the fallback)."""
    ov = (t.get("overrides") or {}).get(cat) or {}
    c = t["categories"][cat]
    if ov.get("use"):
        return ov["use"]
    if c["use"] and c["ready"]:
        return c["use"]
    return c["backup"]

def setup(dry_run=False, refresh=False, use=()):
    """Detect the hardware, pick one tool per category, install the missing
    picks, write ~/.shelf/tools.json. Picks from an earlier setup on this
    computer are kept (so every project uses the same tools) unless refresh.
    `use` (["mineru=/path/to/mineru"]) records tools installed where shelf
    wouldn't look, such as inside another project's virtual environment."""
    paths = {}
    for u in use:
        name, _, path = u.partition("=")
        if not Path(path).is_file():
            sys.exit(f"error: --use {u}: no such file")
        paths[name] = str(Path(path).resolve())
    if paths and not dry_run:  # saved first, so detection below already sees them
        cur = tools_json() or {"categories": {}}
        cur["paths"] = {**(cur.get("paths") or {}), **paths}
        tools_path().parent.mkdir(parents=True, exist_ok=True)
        tools_path().write_text(json.dumps(cur, indent=2) + "\n", encoding=UTF)
    hw, reg, old = hardware(), load_registry(), tools_json()
    keep = {} if refresh or not old else \
        {cat: c["use"] for cat, c in old.get("categories", {}).items() if c.get("use")}
    print(f"machine: {hw['os']}, {hw['ram_gb']} GB RAM, {hw['cpus']} CPUs, "
          f"GPU: {hw['gpu'] or 'none'}" + (f" ({hw['vram_gb']} GB)" if hw['vram_gb'] else "")
          + f" -> tier {hw['tier']}" + ("; keeping earlier picks" if keep else ""))
    picks = choose(reg, hw["tier"], keep)
    runs, admin_pkgs, later, seen = [], {}, [], set()
    for cat, (pick, _) in picks.items():
        if not pick or installed(pick) or pick["name"] in seen:
            continue  # one tool can serve several jobs: install it once
        seen.add(pick["name"])
        use = pick
        mgr, pkg = install_plan(use)
        if mgr in ADMIN_MANAGERS:
            admin_pkgs.setdefault(mgr, []).append(pkg)
        elif mgr != "pip":
            runs.append((use["name"], MANAGERS[mgr][1].format(pkg=pkg)))
        elif shutil.which("uv"):  # its own environment, on PATH, no admin rights
            runs.append((use["name"], f'uv tool install "{pkg}"'))
        elif shutil.which("pipx"):  # same idea
            runs.append((use["name"], f'pipx install "{pkg}"'))
        elif not externally_managed():
            runs.append((use["name"], f'"{sys.executable}" -m pip install "{pkg}"'))
        else:  # e.g. Ubuntu: pipx has to come from the system package manager first
            sysmgr = next((m for m in ADMIN_MANAGERS + ("brew",)
                           if shutil.which(MANAGERS[m][0])), None)
            if sysmgr == "brew":
                runs.insert(0, ("pipx", "brew install pipx"))
                runs.append((use["name"], f'pipx install "{pkg}"'))
            elif sysmgr:
                admin_pkgs.setdefault(sysmgr, []).append(
                    "python-pipx" if sysmgr == "pacman" else "pipx")
                later.append(use["name"])
    admin = " && ".join(MANAGERS[m][1].format(pkg=" ".join(dict.fromkeys(p)))
                        for m, p in admin_pkgs.items())
    for name, cmd in runs:
        print(f"{'would install' if dry_run else 'installing'} {name}: {cmd}")
        if not dry_run and subprocess.run(cmd, shell=True).returncode:
            print(f"  {name}: install failed; its backup/fallback will be used")
    if admin:
        if not dry_run and sys.stdin.isatty():  # a person at a terminal: sudo asks once
            print(f"installing (needs your password): {admin}")
            if subprocess.run(admin, shell=True).returncode == 0:
                admin = ""
        else:
            print(f"needs administrator rights; the user runs once:\n  {admin}")
    if later:
        print(f"after that, run `python3 shelf.py setup` again for: {', '.join(later)}")
    if dry_run:
        print("dry run: nothing installed, tools.json not written")
        return
    show_tools(write_tools_json(hw, reg, choose(reg, hw["tier"], keep) if runs else picks,
                                admin, paths))

def show_tools(t):
    print(f"tools on this computer ({tools_path()}):")
    for cat, c in t["categories"].items():
        use = effective(t, cat)
        note = ("override" if (t.get("overrides") or {}).get(cat, {}).get("use") else
                f"backup: {c['backup']}" if use == c["use"] and c["backup"] else
                f"{c['use']} not installed yet" if c["use"] and not c["ready"] else "")
        shown = use or (f"fallback: {c['fallback']}" if c["fallback"] else "--")
        print(f"  {cat:<9} {shown:<14} {note}".rstrip())

def tools():
    """Show tools.json, refreshing which picks are installed (never installs)."""
    t = tools_json()
    if not t:
        sys.exit("tools not set up on this computer yet: run `python3 shelf.py setup`")
    reg = load_registry()
    by_name = {x["name"]: x for c in reg["categories"].values() for x in c["tools"]}
    for c in t["categories"].values():
        c["ready"] = bool(c["use"] and c["use"] in by_name and installed(by_name[c["use"]]))
    tools_path().write_text(json.dumps(t, indent=2, ensure_ascii=False) + "\n", encoding=UTF)
    show_tools(t)
    try:
        age = (date.today() - date.fromisoformat(reg["updated"])).days
    except Exception:
        age = 0
    if age > REGISTRY_MAX_AGE:
        print(f"note: tools.registry.json is {age} days old; newer tools may exist "
              "(an agent can check, see AGENTS.md)")

MINERU_FIG = re.compile(r"!\[(\w+) block\]\(doc:[^)]*\)")  # MinerU 4's internal figure refs
MINERU_TAGS = re.compile(r"</?(?:small|span|div)\b[^>]*>")

def put_text(p, text, tool):
    """Replace a page's text with a tool's output, keeping page images at the
    top. Returns False if the tool found nothing: earlier text then stays,
    and a page with no text at all becomes a todo."""
    fm, body = read_page(p)
    images = "".join(l + "\n\n" for l in body.splitlines() if l.startswith("![page "))
    text = (text or "").strip()
    if not text:
        earlier = "\n".join(l for l in body.splitlines()
                            if not l.startswith("![page ") and TODO not in l).strip()
        if earlier:
            return False
        text = f"{TODO} {tool} found no text: transcribe from the page image"
    fm["source"] = tool
    write_page(p, fm, images + text + "\n")
    return TODO not in text

def write_ocr(key, texts, tool):
    """put_text() for every page ({pdf page: text}); returns pages left empty."""
    return [int(p.stem) for p in sorted((ITEMS / key / "pages").glob("*.md"))
            if not put_text(p, texts.get(int(p.stem)), tool)]

def extract_mineru(key, d, pdf, exe, force=False):
    """MinerU 4, one page image per call (page numbers can't drift), writing
    each page as soon as it's done, so an interrupted run just continues.
    Returns (pages where nothing was found, pages where MinerU failed), or
    None if there was nothing to do."""
    import tempfile
    pages = sorted((d / "pages").glob("*.md"))
    todo = [p for p in pages if force or read_page(p)[0].get("source") != "mineru"]
    if not todo:
        print(f"{key}: every page already came from mineru (use --force to redo)")
        return None
    full = {int(f.name[1:5]): f for f in (d / "figures").glob("p*-full.png")}
    if len(full) < len(pages):  # page images: MinerU's input, and the target of its figure refs
        tmp = Path(tempfile.mkdtemp(prefix="shelf-"))
        try:
            for i, f in render_pages(pdf, tmp, 200).items():
                full[i] = d / "figures" / f"p{i:04d}-full.png"
                shutil.move(str(f), full[i])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    # MinerU 4 parses through a local background server; start it if needed and
    # stop it afterwards only if we started it (never touch the user's own)
    started = not mineru_status(exe).get("running")
    if started:
        print("  starting the local mineru server ...", flush=True)
        if subprocess.run([exe, "server", "start"], capture_output=True).returncode:
            sys.exit("error: could not start the mineru server (`mineru server start`)")
    empty, failed = [], []
    try:
        wait_for_mineru(exe)
        for n, p in enumerate(todo, 1):
            done = parse_page_mineru(exe, d, p, full)
            if done is False:
                empty.append(int(p.stem))
            elif done is None:
                failed.append(int(p.stem))
            print(f"  p{int(p.stem):04d} {'done' if done else 'empty' if done is False else 'FAILED'}"
                  f" ({n}/{len(todo)})", flush=True)
    finally:
        if started:
            subprocess.run([exe, "server", "stop"], capture_output=True)
    return empty, failed

def mineru_status(exe):
    r = subprocess.run([exe, "server", "status", "--json"], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {}

def wait_for_mineru(exe, timeout=300):
    """The server answers at once, but its local parse engine needs a few
    seconds to load models; parsing before that fails."""
    import time
    end = time.monotonic() + timeout
    while True:
        local = (mineru_status(exe).get("parse_server") or {}).get("local") or {}
        if local.get("healthy"):
            return
        if time.monotonic() > end:
            sys.exit("error: the local mineru engine didn't become ready: "
                     f"{local.get('error_msg') or 'timed out'} (see `mineru server status`)")
        time.sleep(2)

def parse_page_mineru(exe, d, p, full):
    """One page through MinerU: True = text written, False = nothing found,
    None = MinerU failed (the page keeps its earlier text)."""
    import html
    i = int(p.stem)
    out = d / "figures" / f".mineru-p{i:04d}.md"
    r = subprocess.run([exe, "parse", str(full[i]), "--wait", "900", "--no-marker",
                        "-o", str(out)], capture_output=True, text=True)
    if r.returncode or not out.is_file():
        print(f"  p{i:04d}: {(r.stderr or r.stdout).strip()[-200:]}")
        return None
    text = out.read_text(encoding=UTF)
    out.unlink()
    text = MINERU_FIG.sub(lambda m: f"![{m.group(1)} (see page image)](../figures/{full[i].name})",
                          text)
    return put_text(p, html.unescape(MINERU_TAGS.sub("", text)), "mineru")


def extract(key, tool=None, lang="eng", force=False):
    """Fill an item's pages with this computer's local tool for the job
    (`pdf-text` for born-digital PDFs, `scan-ocr` for scans), so the agent
    reviews text instead of reading every page itself."""
    import tempfile
    d = item_dir(key)
    m = load_meta(d)
    if not any(r.endswith(".pdf") for r in m.get("raw") or []):
        sys.exit("error: this item is page images (photos, handwriting): the agent "
                 "transcribes those itself")
    cat = "pdf-text" if m.get("source") == "original" else "scan-ocr"
    t = tools_json()
    tool = (tool or (effective(t, cat) if t and cat in t["categories"] else None)
            or ("pypdf" if cat == "pdf-text" else None))
    if not tool:
        sys.exit(f"error: no OCR tool on this computer (`shelf.py setup`); or run "
                 f"`shelf.py render {key}` and transcribe the page images")
    if tool not in EXTRACTORS:
        sys.exit(f"error: shelf can't run '{tool}' (it runs {', '.join(EXTRACTORS)})")
    if tool == "pypdf":
        print(f"{key}: pypdf text was already written by `add`; nothing to do")
        return
    exe = find_exe(tool)
    if not exe:
        sys.exit(f"error: {tool} is not installed (`shelf.py setup`)")
    pdf = raw_pdf(d, key)
    print(f"{key}: running {tool} locally on {pdf.name} ...")
    if tool == "mineru":
        res = extract_mineru(key, d, pdf, exe, force)
        if res:
            finish_extract(key, d, tool, lang, len(list((d / "pages").glob("*.md"))), *res)
        return
    tmp = Path(tempfile.mkdtemp(prefix="shelf-"))
    try:
        if tool == "ocrmypdf":
            out = tmp / "ocr.pdf"
            r = subprocess.run([exe, "--force-ocr", "-q", "-l", lang, str(pdf), str(out)],
                               capture_output=True, text=True)
            if r.returncode:
                sys.exit(f"error: ocrmypdf failed: {r.stderr.strip()[-400:]}")
            from pypdf import PdfReader
            texts = {i: pg.extract_text() or ""
                     for i, pg in enumerate(PdfReader(str(out)).pages, 1)}
        else:  # tesseract, one rendered page at a time
            texts = {}
            for i, png in render_pages(pdf, tmp, 300).items():
                r = subprocess.run([exe, str(png), "stdout", "-l", lang],
                                   capture_output=True, text=True)
                texts[i] = r.stdout if r.returncode == 0 else ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    finish_extract(key, d, tool, lang, len(texts), write_ocr(key, texts, tool))

def finish_extract(key, d, tool, lang, n, empty, failed=()):
    """Log the run; status follows the pages: `extracted` once no page needs
    work, back to `pending` while any page still has a shelf:todo."""
    m = load_meta(d)
    left = [int(p.stem) for p in sorted((d / "pages").glob("*.md")) if TODO in read_page(p)[1]]
    status = ("pending" if left else
              "extracted" if m.get("status") in ("pending", "partial") else m.get("status"))
    if status != m.get("status"):
        m["status"] = status
        save_meta(d, m)
    log_append(d, f"shelf.py extract ({tool})", [
        f"{n} pages run through {tool} locally"
        + ("" if tool == "mineru" else f" (language {lang})")
        + ", replacing the earlier text; agent should spot-check against the page images",
        *([f"{tool} found nothing on pages {empty}: earlier text kept, or marked "
           "shelf:todo where there was none"] if empty else []),
        *([f"{tool} failed on pages {list(failed)}: they keep their earlier text; "
           "run extract again to retry"] if failed else [])])
    print(f"{key}: {tool} done" + (f"; nothing found on pages {empty}" if empty else "")
          + (f"; FAILED on pages {list(failed)} (earlier text kept; run again to retry)"
             if failed else "") + f"; status {m.get('status')}")

def doctor():
    import importlib.util
    print(f"shelf.py {__version__} (spec {SPEC_VERSION}), python {sys.version.split()[0]}")
    for mod in ("pypdf", "yaml"):
        print(f"  {'OK     ' if importlib.util.find_spec(mod) else 'MISSING'} python:{mod}")
    t = tools_json()
    if not t:
        print("tools: not set up: run `python3 shelf.py setup` (AGENTS.md, Tools)")
    else:
        print("tools: " + ", ".join(f"{cat}={effective(t, cat) or 'fallback'}"
                                   for cat in t["categories"]))
        if t.get("pending_admin"):
            print(f"  waiting for the user to run: {t['pending_admin']}")

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
                "image (HEIC: add-scan makes a readable copy)" if ext in HEIC_EXT else
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
    sub.add_parser("doctor", help="deps, tools, waiting inbox files, library health")
    p = sub.add_parser("setup", help="pick and install the tools for this computer")
    p.add_argument("--dry-run", action="store_true", help="show the plan, install nothing")
    p.add_argument("--refresh", action="store_true",
                   help="pick again from scratch instead of keeping earlier picks")
    p.add_argument("--use", action="append", default=[], metavar="TOOL=PATH",
                   help="a tool installed where shelf doesn't look (e.g. in a venv)")
    sub.add_parser("tools", help="show this computer's tools (~/.shelf/tools.json)")
    p = sub.add_parser("extract", help="fill an item's pages with the local tool")
    p.add_argument("key"); p.add_argument("--tool", choices=EXTRACTORS)
    p.add_argument("--lang", default="eng", help="OCR language(s), e.g. eng+deu")
    p.add_argument("--force", action="store_true", help="mineru: redo pages already done")
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
    elif a.cmd == "setup": setup(a.dry_run, a.refresh, a.use)
    elif a.cmd == "extract": extract(a.key, a.tool, a.lang, a.force)
    elif a.cmd == "tools": tools()
    elif a.cmd == "add":
        add(a.pdf, key=a.key, source=a.source, page_offset=a.page_offset,
            force=a.force, converted_from=a.converted_from, **meta)
    elif a.cmd == "add-scan":
        add_scan(a.images, a.key, source=a.source, force=a.force, **meta)
    elif a.cmd == "render": render(a.key, a.dpi)
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
