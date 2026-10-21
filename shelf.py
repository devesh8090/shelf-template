#!/usr/bin/env python3
"""shelf.py - reference implementation of the shelf library format (v0.3).

The format is defined in SPEC.md; the agent workflow in AGENTS.md.

Commands:
  init                              create inbox/ and items/ here
  add <file.pdf> --key K [...]      new item: hash, scaffold, pypdf extraction
  add-scan <img...> --key K [...]   new item from page images (agent transcribes)
  import-mineru <key> <content_list.json>
                                    replace page text with MinerU output
  apply-toc <key>                   copy sections from toc.md into page frontmatter
  rekey <old> <new>                 rename an item and fix every key: field
  page <key> <n>                    print path of pages/NNNN.md
  verify <key> <pdf_page> "quote"   is this quote really on that page?
  find "quote" [--key K]            which pages contain it? (prints citations)
  lint [key...]                     check items against SPEC.md
  catalog                           regenerate catalog.md
  bib [key...]                      print BibTeX entries
  doctor                            check deps, inbox and item statuses

The agent drives the judgment work (metadata, toc, fixing extraction);
this script does the deterministic parts. Deps: pypdf, pyyaml.
Run any command with --help. On Windows use `py` instead of `python3`."""
import argparse, hashlib, json, re, shutil, sys, unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

__version__ = "0.3.0"
SPEC_VERSION = "1.0"

ROOT = Path(__file__).resolve().parent
INBOX, ITEMS = ROOT / "inbox", ROOT / "items"
UTF = "utf-8"

STATUSES = ("pending", "partial", "extracted", "verified")
TYPES = ("paper", "book", "chapter", "thesis", "notes", "slides", "cheatsheet", "misc")
SOURCES = ("original", "scan", "photocopy")
PAGE_SOURCES = ("mineru", "marker", "pypdf", "agent-transcription", "manual")
REQUIRED = ("key", "title", "authors", "type", "year", "sha256", "source",
            "status", "pages_pdf", "page_offset", "added", "tags")
PAGE_FIELDS = ("key", "pdf_page", "printed_page", "section", "source")
BIB_FIELDS = ("doi", "url", "journal", "booktitle", "publisher", "volume",
              "number", "pages", "edition", "isbn")
BIBTYPE = {"paper": "article", "book": "book", "chapter": "incollection",
           "thesis": "phdthesis"}
KEY_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TODO = "<!-- shelf:todo -->"
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

# what PDFs print vs. what people type when they copy a quote
PUNCT = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                       "–": "-", "—": "-", "−": "-", "­": ""})

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

def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

def free_key(k):
    base, n = k, 2
    while (ITEMS / k).exists():
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
    """Return (frontmatter dict, body) of a pages/NNNN.md file."""
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

def known_hashes():
    out = {}
    for d in items():
        try:
            out[load_meta(d).get("sha256")] = d.name
        except Exception:
            pass
    return out

def page_path(key, n):
    return ITEMS / key / "pages" / f"{int(n):04d}.md"

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

def new_meta(k, title, sha, source, status, pages, page_offset, meta):
    m = {"key": k, "title": meta.get("title") or title,
         "authors": meta.get("authors") or [], "type": meta.get("type") or "misc",
         "year": meta.get("year"), "sha256": sha, "source": source,
         "status": status, "pages_pdf": pages, "page_offset": page_offset,
         "added": date.today().isoformat(), "tags": meta.get("tags") or []}
    if meta.get("doi"):
        m["doi"] = meta["doi"]
    return m

def build(k, fill):
    """Build an item in items/.tmp-<key> and move it into place only on success,
    so a crash never leaves a half-made item behind."""
    ITEMS.mkdir(exist_ok=True)
    tmp = ITEMS / f".tmp-{k}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        (tmp / "pages").mkdir(parents=True)
        (tmp / "figures").mkdir()
        fill(tmp)
        tmp.rename(ITEMS / k)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

def toc_stub(title):
    return (f"# {title}\n\n{TODO} fill in: section -> pdf page ranges\n\n"
            "| Section | pdf pages |\n|---|---|\n")

# ---------------------------------------------------------------- commands

def init():
    for p in ("inbox", "items"):
        (ROOT / p).mkdir(exist_ok=True)
    print("shelf structure ready in", ROOT)

def add(pdf_path, key=None, source="original", page_offset=None, force=False, **meta):
    from pypdf import PdfReader
    pdf = Path(pdf_path)
    if not pdf.is_file():
        sys.exit(f"error: no such file: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        sys.exit(f"error: {pdf.name} is not a PDF. For page images use "
                 "`shelf.py add-scan` (see AGENTS.md).")
    try:
        reader = PdfReader(str(pdf))
    except Exception as e:
        sys.exit(f"error: cannot read PDF: {e}")
    if reader.is_encrypted and reader.decrypt("") == 0:
        sys.exit("error: password-protected PDF (unlock it first).")
    h = sha256(pdf)
    dup = known_hashes().get(h)
    if dup and not force:
        sys.exit(f"error: this PDF is already in the library as items/{dup} "
                 "(use --force to add a second copy)")

    # str(): pypdf returns its own str subclass, which yaml refuses to dump
    title = str((reader.metadata.title if reader.metadata else "") or pdf.stem)
    k = free_key(slug(key) if key else
                 "doc-" + ("-".join(slug(title).split("-")[:4]) or "untitled"))
    labels = page_labels(reader)
    failed = []

    def fill(d):
        shutil.copy2(pdf, d / "source.pdf")
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
            body = text.rstrip() + "\n" + "".join(
                f"\n![fig {Path(f).stem}](../figures/{f})\n" for f in figs)
            write_page(d / "pages" / f"{i:04d}.md",
                       {"key": k, "pdf_page": i, "printed_page": printed,
                        "section": None, "source": "pypdf"}, body)
        save_meta(d, new_meta(k, title, h, source, "partial" if failed else "extracted",
                              len(reader.pages), page_offset, meta))
        (d / "toc.md").write_text(toc_stub(meta.get("title") or title), encoding=UTF)

    build(k, fill)
    print(f"created items/{k} ({len(reader.pages)} pages"
          + (f", {len(failed)} failed: {failed}" if failed else "")
          + (", printed page numbers from PDF labels" if labels else "") + ")")
    if not key:
        print(f"note: '{k}' is a provisional key; rename with "
              f"`shelf.py rekey {k} <surname><year>-<title-words>`")
    print("next: fix metadata in item.yaml, fill toc.md, `shelf.py apply-toc`, "
          "then `shelf.py verify ... --mark`")
    return k

def add_scan(images, key, source="scan", force=False, **meta):
    imgs = sorted(Path(p) for p in images)
    for p in imgs:
        if not p.is_file():
            sys.exit(f"error: no such file: {p}")
        if p.suffix.lower() not in IMG_EXT:
            sys.exit(f"error: {p.name}: not an image ({', '.join(sorted(IMG_EXT))})")
    h = hashlib.sha256("".join(sha256(p) for p in imgs).encode()).hexdigest()
    dup = known_hashes().get(h)
    if dup and not force:
        sys.exit(f"error: these images are already in the library as items/{dup}")
    k = free_key(slug(key))

    def fill(d):
        for i, p in enumerate(imgs, 1):
            name = f"p{i:04d}-full{p.suffix.lower()}"
            shutil.copy2(p, d / "figures" / name)
            write_page(d / "pages" / f"{i:04d}.md",
                       {"key": k, "pdf_page": i, "printed_page": None,
                        "section": None, "source": "agent-transcription"},
                       f"![page {i}](../figures/{name})\n\n{TODO} transcribe this page\n")
        meta.setdefault("type", "notes")
        save_meta(d, new_meta(k, k, h, source, "pending", len(imgs), None, meta))
        (d / "toc.md").write_text(toc_stub(meta.get("title") or k), encoding=UTF)

    build(k, fill)
    print(f"created items/{k} ({len(imgs)} page images, status: pending)")
    print("next: transcribe each pages/NNNN.md (replace the shelf:todo line), "
          "then set status: extracted")
    return k

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
        write_page(p, fm, "\n\n".join(x for x in parts if x) + "\n")
    if m.get("status") in ("pending", "partial"):
        m["status"] = "extracted"
        save_meta(d, m)
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
    src.rename(ITEMS / new)
    print(f"renamed items/{old} -> items/{new}")

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
    if not isinstance(m.get("authors", []), list) or not isinstance(m.get("tags", []), list):
        err.append("item.yaml: authors and tags must be lists")
    if m.get("page_offset") is not None and not isinstance(m.get("page_offset"), int):
        err.append("item.yaml: page_offset must be an integer or empty")
    if not m.get("authors") or not m.get("year"):
        (err if st == "verified" else warn).append("item.yaml: authors/year not filled in")

    pdf = d / "source.pdf"
    if pdf.exists():
        if check_hash and m.get("sha256") != sha256(pdf):
            err.append("sha256 does not match source.pdf (was it modified?)")
    elif m.get("source") == "original":
        err.append("source.pdf missing")

    pages = sorted((d / "pages").glob("*.md"))
    if [p.name for p in pages] != [f"{i:04d}.md" for i in range(1, len(pages) + 1)]:
        err.append("pages/ must be 0001.md ... NNNN.md with no gaps")
    if m.get("pages_pdf") != len(pages):
        err.append(f"pages_pdf is {m.get('pages_pdf')} but pages/ has {len(pages)} files")
    todo = empty = 0
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
        if fm.get("source") not in PAGE_SOURCES:
            err.append(f"{p.name}: source '{fm.get('source')}' not one of {', '.join(PAGE_SOURCES)}")
        for ref in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", body):
            if "://" not in ref and not (p.parent / ref).exists():
                err.append(f"{p.name}: broken image link {ref}")
        todo += TODO in body
        empty += not body.strip()
    if todo and done:
        err.append(f"{todo} page(s) still contain {TODO} but status is {st}")
    if empty:
        warn.append(f"{empty} page(s) have no text (blank, figure-only, or needs OCR?)")

    toc = d / "toc.md"
    if not toc.exists():
        err.append("toc.md missing")
    elif TODO in toc.read_text(encoding=UTF):
        (err if st == "verified" else warn).append("toc.md not filled in yet")
    return err, warn

def lint(keys=None):
    dirs = [item_dir(k) for k in keys] if keys else items()
    bad = 0
    for d in dirs:
        errs, warns = lint_item(d)
        print(("FAIL " if errs else "ok   ") + d.name)
        for e in errs:
            print("  error:", e)
        for w in warns:
            print("  warn: ", w)
        bad += bool(errs)
    for t in ITEMS.glob(".tmp-*"):
        print(f"warn: leftover {t.name} from an interrupted add (safe to delete)")
    print(f"{len(dirs)} item(s), {bad} with errors")
    sys.exit(1 if bad else 0)

def catalog():
    rows = []
    for d in items():
        try:
            m = load_meta(d)
        except Exception as e:
            rows.append(f"| ~~{d.name}~~ | unreadable item.yaml: {e} | | | | | |")
            continue
        au = m.get("authors") or []
        au = "; ".join(au[:2]) + (" et al." if len(au) > 2 else "")
        cells = [f"`{d.name}`", m.get("title"), au, m.get("year"), m.get("type"),
                 m.get("pages_pdf"), m.get("status")]
        rows.append("| " + " | ".join(str(c if c is not None else "").replace("|", "\\|")
                                      for c in cells) + " |")
    out = ["# Catalog", "", "<!-- generated by `python3 shelf.py catalog`; do not edit -->", ""]
    if rows:
        out += ["| key | title | authors | year | type | pages | status |",
                "|---|---|---|---|---|---|---|", *rows]
    else:
        out += ["No items yet. Drop a PDF into `inbox/` and tell your agent:",
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
    for cmd in ("mineru", "marker_single", "tesseract", "pdftoppm"):
        print(f"  {'OK     ' if shutil.which(cmd) else '--     '} cli:{cmd} (optional)")
    hashes = known_hashes()
    files = sorted(f for f in INBOX.iterdir() if f.name[0] != ".") if INBOX.is_dir() else []
    print(f"inbox/: {len(files)} file(s)")
    for f in files:
        if f.suffix.lower() == ".pdf":
            k = hashes.get(sha256(f))
            print(f"  {f.name}: " + (f"already added as {k}" if k else "NOT PROCESSED"))
        elif f.suffix.lower() in IMG_EXT:
            print(f"  {f.name}: page image (use add-scan)")
        else:
            print(f"  {f.name}: unsupported file type")
    its = items()
    counts = Counter()
    for d in its:
        try:
            counts[load_meta(d).get("status")] += 1
        except Exception:
            counts["unreadable"] += 1
    print(f"items/: {len(its)} item(s)" +
          (" (" + ", ".join(f"{v} {k}" for k, v in sorted(counts.items(), key=str)) + ")"
           if its else ""))
    if its:
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

    sub.add_parser("init", help="create inbox/ and items/")
    p = sub.add_parser("add", help="create an item from a PDF")
    p.add_argument("pdf")
    p.add_argument("--key", help="e.g. vaswani2017-attention-is-all-you (see SPEC.md)")
    p.add_argument("--source", default="original", choices=SOURCES)
    p.add_argument("--page-offset", type=int,
                   help="printed page = pdf page - N (only if the PDF has no page labels)")
    meta_args(p, "misc")
    p = sub.add_parser("add-scan", help="create an item from page images")
    p.add_argument("images", nargs="+")
    p.add_argument("--key", required=True)
    p.add_argument("--source", default="scan", choices=SOURCES)
    meta_args(p, "notes")
    p = sub.add_parser("import-mineru", help="replace page text with MinerU output")
    p.add_argument("key"); p.add_argument("content_list", help="MinerU *_content_list.json")
    p = sub.add_parser("apply-toc", help="copy sections from toc.md into pages")
    p.add_argument("key")
    p = sub.add_parser("rekey", help="rename an item")
    p.add_argument("old"); p.add_argument("new")
    p = sub.add_parser("page", help="print path of pages/NNNN.md")
    p.add_argument("key"); p.add_argument("n", type=int)
    p = sub.add_parser("verify", help="check a quote is on a given pdf page")
    p.add_argument("key"); p.add_argument("pdf_page", type=int); p.add_argument("quote")
    p.add_argument("--mark", action="store_true",
                   help="on success, set status: verified (if the item passes lint)")
    p = sub.add_parser("find", help="list pages containing a quote, as citations")
    p.add_argument("quote"); p.add_argument("--key")
    p = sub.add_parser("lint", help="check items against SPEC.md")
    p.add_argument("keys", nargs="*")
    sub.add_parser("catalog", help="regenerate catalog.md")
    p = sub.add_parser("bib", help="print BibTeX for items")
    p.add_argument("keys", nargs="*")
    sub.add_parser("doctor", help="check deps, inbox, item statuses")

    a = ap.parse_args()
    meta = {f: getattr(a, f, None) for f in ("title", "authors", "year", "type", "tags", "doi")}
    if a.cmd == "init": init()
    elif a.cmd == "add":
        add(a.pdf, key=a.key, source=a.source, page_offset=a.page_offset,
            force=a.force, **meta)
    elif a.cmd == "add-scan":
        add_scan(a.images, a.key, source=a.source, force=a.force, **meta)
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
    elif a.cmd == "doctor": doctor()

if __name__ == "__main__":
    main()
