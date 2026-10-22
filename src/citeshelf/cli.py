"""citeshelf - an AI-agent library you can cite to the exact page (v0.6).

Four levels (SPEC.md); the agent workflow is in AGENTS.md.
  L1 inbox/   the human dumps anything here, in any folders
  L2 raw/     every original, flat, renamed, never modified (+ manifest.yaml)
  L3 items/   one folder per document, one markdown file per page: ground truth
  L4 mapped/  the agent's organised, de-duplicated notes, citing L3

Commands (run inside a library, or pass --library PATH):
  init [PATH]                       make PATH (default: here) a library
  upgrade                           refresh AGENTS.md/SPEC.md to this citeshelf version
  doctor                            tools, waiting inbox files, library health
  setup [--dry-run] [--use T=PATH]  install the extraction tool (MinerU) for this computer
  tools                             what this computer uses for each job
  add <file.pdf> --key K [...]      L1->L2->L3 for a PDF (moves it out of inbox/)
  add-scan <img...> --key K [...]   same for page images (also iPhone HEIC), in order
  extract <key>                     fill an item's pages with MinerU, locally
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
mapped/); citeshelf does the deterministic parts."""
import argparse, hashlib, json, re, shutil, subprocess, sys, unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

from citeshelf import __version__

SPEC_VERSION = "2.1"
MARKER = ".citeshelf"  # marks a library's root folder
TEMPLATES = Path(__file__).resolve().parent / "templates"
TEMPLATE_FILES = ("AGENTS.md", "CLAUDE.md", "SPEC.md")

# the library being worked on; set by use_library()
ROOT = INBOX = RAW = ITEMS = MAPPED = None

def use_library(root):
    global ROOT, INBOX, RAW, ITEMS, MAPPED
    ROOT = Path(root).resolve()
    INBOX, RAW, ITEMS, MAPPED = (ROOT / x for x in ("inbox", "raw", "items", "mapped"))

def find_library(start):
    """The nearest folder at or above `start` that holds a .citeshelf marker."""
    start = Path(start).resolve()
    return next((d for d in (start, *start.parents) if (d / MARKER).is_file()), None)

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
                 "# Written by citeshelf -- do not edit by hand.\n")

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

LIBRARY_GITIGNORE = """# citeshelf: items/ and mapped/ are text and safe to commit. The originals
# usually are not (copyright, size), so they stay local. To commit them too,
# delete the raw/ lines.
inbox/*
raw/*
!raw/manifest.yaml
items/.tmp-*
*.old
.mineru-service.lock
.DS_Store
"""

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
        sys.exit(f"error: no item '{key}' (see catalog.md or `shelf doctor`)")
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

def write_marker():
    (ROOT / MARKER).write_text(f"citeshelf: {__version__}\nspec: \"{SPEC_VERSION}\"\n",
                               encoding=UTF)

def library_version():
    import yaml
    try:
        return str(yaml.safe_load((ROOT / MARKER).read_text(encoding=UTF)).get("citeshelf"))
    except Exception:
        return None

def init(path="."):
    """Make `path` a library: the four level folders, the agent's rules
    (AGENTS.md, CLAUDE.md, SPEC.md) and the mapped/ entry files. Existing
    files are left alone, so running it again is safe."""
    root = Path(path).resolve()
    outer = find_library(root.parent) if root.parent != root else None
    if outer:
        sys.exit(f"error: {root} is inside the library {outer}")
    root.mkdir(parents=True, exist_ok=True)
    use_library(root)
    for p in (INBOX, RAW, ITEMS, MAPPED):
        p.mkdir(exist_ok=True)
    for name, text in MAPPED_TEMPLATES.items():
        if not (MAPPED / name).exists():
            (MAPPED / name).write_text(text, encoding=UTF)
    for name in TEMPLATE_FILES:
        if not (ROOT / name).exists():
            shutil.copy(TEMPLATES / name, ROOT / name)
    if not (ROOT / ".gitignore").exists():
        (ROOT / ".gitignore").write_text(LIBRARY_GITIGNORE, encoding=UTF)
    if not (ROOT / MARKER).exists():
        write_marker()
    if not (ROOT / "catalog.md").exists():
        catalog()
    print(f"library ready in {ROOT}")
    print('next: drop files into inbox/ and tell your agent: "Process inbox/ per AGENTS.md."')

def upgrade():
    """Bring the library's agent rules up to this citeshelf version. A file
    that was edited by hand is kept next to it as <name>.old."""
    for name in TEMPLATE_FILES:
        new, dst = (TEMPLATES / name).read_text(encoding=UTF), ROOT / name
        if dst.exists() and dst.read_text(encoding=UTF) != new:
            shutil.copy(dst, ROOT / f"{name}.old")
            print(f"updated {name} (previous copy: {name}.old)")
        dst.write_text(new, encoding=UTF)
    old = library_version()
    write_marker()
    print(f"library now on citeshelf {__version__}" + (f" (was {old})" if old != __version__ else ""))

def add(pdf_path, key=None, source="original", page_offset=None, force=False,
        converted_from=None, **meta):
    from pypdf import PdfReader
    pdf = Path(pdf_path)
    if not pdf.is_file():
        sys.exit(f"error: no such file: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        sys.exit(f"error: {pdf.name} is not a PDF. For page images use `shelf "
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
                body += f"\n{TODO} OCR or transcribe this page (`shelf render {k}`)\n"
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
        log_append(d, "shelf add", origins(placed) + [
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
        print(f"note: '{k}' is a provisional key; rename with `shelf rekey {k} <key>`")
    return k

def heic_to_jpg(src, dst):
    """A readable JPG copy of an iPhone HEIC photo."""
    import pillow_heif
    from PIL import Image
    pillow_heif.register_heif_opener()
    with Image.open(src) as im:
        im.convert("RGB").save(dst, "JPEG", quality=92)

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
    k = free_key(slug(key), imgs)
    placed = plan_raw(imgs, [f"{k}-p{i:03d}{p.suffix.lower()}"
                             for i, p in enumerate(imgs, 1)], force)

    def fill(d):
        for i, (src, name, _) in enumerate(placed, 1):
            link = f"../../../raw/{name}"
            if src.suffix.lower() in HEIC_EXT:  # original kept in raw/, readable copy here
                jpg = d / "figures" / f"p{i:04d}-full.jpg"
                try:
                    heic_to_jpg(src, jpg)
                except Exception as e:
                    sys.exit(f"error: could not read {src.name}: {e}")
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
        log_append(d, "shelf add-scan", origins(placed) + [
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
    """Render every page of `pdf` into `out` as p-<n>.png; returns {pdf page: path}."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    files = {}
    try:
        for i in range(len(doc)):
            files[i + 1] = out / f"p-{i + 1}.png"
            doc[i].render(scale=dpi / 72).to_pil().save(files[i + 1])
    finally:
        doc.close()
    return files

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
    log_append(d, "shelf render", [f"{n} page images at {dpi} dpi -> figures/pNNNN-full.png"])
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
    log_append(ITEMS / new, "shelf rekey", [f"renamed from {old} (raw/ file names unchanged)"])
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
    out = ["# Catalog", "", "<!-- generated by `shelf catalog`; do not edit -->", ""]
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

# One tool per job, pinned to the version this release was tested with.
# Moving to a newer or better tool is a new citeshelf release, never a surprise.
MINERU_VERSION = "4.0.12"
MIN_RAM_GB = 7.5  # an "8 GB" machine reports ~7.6; MinerU needs about that

def platform_key():
    return ("win32" if sys.platform.startswith("win") else
            "darwin" if sys.platform == "darwin" else "linux")

def config_path():
    """Per-computer settings, shared by every library on it."""
    import os
    return Path(os.environ.get("CITESHELF_HOME") or Path.home() / ".citeshelf") / "config.json"

def load_config():
    try:
        return json.loads(config_path().read_text(encoding=UTF))
    except Exception:
        return {}

def save_config(c):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(c, indent=2) + "\n", encoding=UTF)

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

def extra_bin_dirs():
    """Where user-level installs land when they aren't on PATH: `uv tool` and
    pipx (~/.local/bin), and the usual conda/miniforge base folders."""
    home = Path.home()
    return [home / ".local" / "bin",
            *[home / base / ("Scripts" if platform_key() == "win32" else "bin")
              for base in ("miniforge3", "miniconda3", "anaconda3")],
            Path("/opt/conda/bin")]

def find_exe(name):
    """Full path of a program: a path given to `setup --use`, else PATH,
    else the usual user-level install folders."""
    import os
    known = (load_config().get("paths") or {}).get(name)
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

def tool_version(exe):
    r = subprocess.run([exe, "--version"], capture_output=True, text=True)
    hit = re.search(r"(\d+\.\d+\.\d+)", r.stdout + r.stderr)
    return hit.group(1) if hit else None

def setup(dry_run=False, use=(), force=False):
    """Install the one extraction tool (MinerU, pinned) with uv. Everything
    else citeshelf needs is already in its own Python dependencies."""
    cfg = load_config()
    for u in use:
        name, _, path = u.partition("=")
        if not Path(path).is_file():
            sys.exit(f"error: --use {u}: no such file")
        cfg.setdefault("paths", {})[name] = str(Path(path).resolve())
    if use and not dry_run:
        save_config(cfg)
    ram = ram_gb()
    print(f"computer: {platform_key()}, {ram:.1f} GB RAM")
    exe = find_exe("mineru")
    ver = tool_version(exe) if exe else None
    if ver == MINERU_VERSION or (exe and use):
        print(f"mineru {ver} ready: {exe}"
              + ("" if ver == MINERU_VERSION else f" (your choice; tested: {MINERU_VERSION})"))
    elif ram and ram < MIN_RAM_GB and not force:
        print(f"skipping mineru: it needs about 8 GB of RAM ({ram:.1f} GB here). PDFs keep "
              "their plain text and the agent reads scans itself.\n"
              "  install anyway: `shelf setup --force`")
    else:
        uv = shutil.which("uv")
        if not uv:
            sys.exit("error: citeshelf installs its tools with uv; install uv first: "
                     "https://docs.astral.sh/uv/getting-started/installation/")
        cmd = [uv, "tool", "install", *(["--force"] if exe else []),
               f"mineru=={MINERU_VERSION}"]
        print(("would run: " if dry_run else "installing: ") + " ".join(cmd[1:])
              + (f"  (replacing mineru {ver})" if exe else ""))
        if dry_run:
            return
        if subprocess.run(cmd).returncode:
            sys.exit("error: installing mineru failed (see above)")
        bindir = subprocess.run([uv, "tool", "dir", "--bin"], capture_output=True,
                                text=True).stdout.strip()
        if bindir and (Path(bindir) / "mineru").exists():
            cfg.setdefault("paths", {})["mineru"] = str(Path(bindir) / "mineru")
        save_config(cfg)
        print("mineru installed. Its first run downloads its models (several GB).")
    if not dry_run:
        tools()

def tools():
    """What this computer uses for each job."""
    exe = find_exe("mineru")
    ver = tool_version(exe) if exe else None
    office = find_exe("soffice") or find_exe("libreoffice")
    print("tools on this computer:")
    print("  PDFs and scans   " + (f"mineru {ver} ({exe})" if exe else
                                    "mineru not installed: plain PDF text only, the agent "
                                    "reads scans (`shelf setup`)"))
    if exe and ver != MINERU_VERSION:
        print(f"                   note: tested with mineru {MINERU_VERSION}")
    print("  page images      built in (pypdfium2)")
    print("  iPhone photos    built in (pillow-heif)")
    print("  office files     " + (office or "LibreOffice not found: ask for a PDF (optional)"))
    print("  handwriting      the agent")

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
    if started:  # started from citeshelf's own folder: MinerU leaves files where it starts
        print("  starting the local mineru server ...", flush=True)
        config_path().parent.mkdir(parents=True, exist_ok=True)
        if subprocess.run([exe, "server", "start"], capture_output=True,
                          cwd=config_path().parent).returncode:
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


def extract(key, tool=None, force=False):
    """Fill an item's pages with MinerU, locally, so the agent reviews text
    instead of reading every page itself. Without MinerU, born-digital PDFs
    keep the plain text `add` wrote, and scans are left to the agent."""
    d = item_dir(key)
    m = load_meta(d)
    if not any(r.endswith(".pdf") for r in m.get("raw") or []):
        sys.exit("error: this item is page images (photos, handwriting): the agent "
                 "transcribes those itself")
    exe = find_exe("mineru")
    tool = tool or ("mineru" if exe else "pypdf")
    if tool == "pypdf":
        if m.get("source") != "original":
            sys.exit(f"error: MinerU isn't installed (`shelf setup`), so the agent reads this "
                     f"scan: run `shelf render {key}` and transcribe the page images")
        print(f"{key}: plain pypdf text was already written by `add`; nothing to do")
        return
    if not exe:
        sys.exit("error: MinerU isn't installed: run `shelf setup`")
    pdf = raw_pdf(d, key)
    print(f"{key}: running mineru locally on {pdf.name} ...")
    res = extract_mineru(key, d, pdf, exe, force)
    if res:
        finish_extract(key, d, "mineru", len(list((d / "pages").glob("*.md"))), *res)

def finish_extract(key, d, tool, n, empty, failed=()):
    """Log the run; status follows the pages: `extracted` once no page needs
    work, back to `pending` while any page still has a shelf:todo."""
    m = load_meta(d)
    left = [int(p.stem) for p in sorted((d / "pages").glob("*.md")) if TODO in read_page(p)[1]]
    status = ("pending" if left else
              "extracted" if m.get("status") in ("pending", "partial") else m.get("status"))
    if status != m.get("status"):
        m["status"] = status
        save_meta(d, m)
    log_append(d, f"shelf extract ({tool})", [
        f"{n} pages run through {tool} locally, replacing the earlier text; "
        "agent should spot-check against the page images",
        *([f"{tool} found nothing on pages {empty}: earlier text kept, or marked "
           "shelf:todo where there was none"] if empty else []),
        *([f"{tool} failed on pages {list(failed)}: they keep their earlier text; "
           "run extract again to retry"] if failed else [])])
    print(f"{key}: {tool} done" + (f"; nothing found on pages {empty}" if empty else "")
          + (f"; FAILED on pages {list(failed)} (earlier text kept; run again to retry)"
             if failed else "") + f"; status {m.get('status')}")

def doctor():
    print(f"citeshelf {__version__} (spec {SPEC_VERSION}), library {ROOT}")
    lv = library_version()
    if lv != __version__:
        print(f"  this library's AGENTS.md/SPEC.md are from citeshelf {lv}: run `shelf upgrade`")
    exe = find_exe("mineru")
    ver = tool_version(exe) if exe else None
    print("tools: " + (f"mineru {ver}" if exe else "mineru not installed: run `shelf setup`")
          + (f" (tested: {MINERU_VERSION})" if exe and ver != MINERU_VERSION else ""))

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
        print("run `shelf lint` for details")

# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(prog="shelf", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version",
                    version=f"citeshelf {__version__} (spec {SPEC_VERSION})")
    ap.add_argument("-C", "--library", metavar="PATH",
                    help="the library to work on (default: the one you're in)")
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

    p = sub.add_parser("init", help="make a folder a library")
    p.add_argument("path", nargs="?", default=".")
    sub.add_parser("upgrade", help="refresh AGENTS.md/SPEC.md to this citeshelf version")
    sub.add_parser("doctor", help="tools, waiting inbox files, library health")
    p = sub.add_parser("setup", help="install the extraction tool (MinerU) for this computer")
    p.add_argument("--dry-run", action="store_true", help="show the plan, install nothing")
    p.add_argument("--use", action="append", default=[], metavar="TOOL=PATH",
                   help="use a tool installed elsewhere (e.g. mineru in a venv)")
    p.add_argument("--force", action="store_true", help="install even on a small machine")
    sub.add_parser("tools", help="what this computer uses for each job")
    p = sub.add_parser("extract", help="fill an item's pages with MinerU, locally")
    p.add_argument("key"); p.add_argument("--tool", choices=("mineru", "pypdf"))
    p.add_argument("--force", action="store_true", help="redo pages MinerU already did")
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
    p = sub.add_parser("render", help="page images of a scanned PDF, for transcription")
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
    if a.cmd == "init":
        return init(a.path)
    if a.cmd == "setup":
        return setup(a.dry_run, a.use, a.force)
    if a.cmd == "tools":
        return tools()
    root = Path(a.library).resolve() if a.library else find_library(Path.cwd())
    if not root or not (root / MARKER).is_file():
        sys.exit("error: not inside a citeshelf library: run `shelf init` here first, "
                 "or pass --library PATH")
    use_library(root)
    if a.cmd == "upgrade": upgrade()
    elif a.cmd == "doctor": doctor()
    elif a.cmd == "extract": extract(a.key, a.tool, a.force)
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
