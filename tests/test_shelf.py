import json, shutil, subprocess, sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelf  # noqa: E402

SHELF = Path(__file__).resolve().parents[1] / "shelf.py"
UTF = "utf-8"


@pytest.fixture(autouse=True)
def lib(tmp_path, monkeypatch):
    """An empty library rooted in tmp_path. autouse: no test may ever touch
    the real library this file lives in."""
    monkeypatch.setattr(shelf, "ROOT", tmp_path)
    for name in ("INBOX", "RAW", "ITEMS", "MAPPED"):
        monkeypatch.setattr(shelf, name, tmp_path / name.lower())
    shelf.init()
    return tmp_path


def blank_pdf(path, n=2, labels=False):
    """A blank PDF; its path goes into the metadata so every file is unique."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(612, 792)
    w.add_metadata({"/Subject": str(path)})
    if labels:  # i, then 1, 2, ...
        w.set_page_label(0, 0, "/r")
        w.set_page_label(1, n - 1, "/D", start=1)
    with open(path, "wb") as fh:
        w.write(fh)
    return path


def set_body(key, n, body):
    p = shelf.page_path(key, n)
    fm, _ = shelf.read_page(p)
    shelf.write_page(p, fm, body)


def meta(key):
    return shelf.load_meta(shelf.ITEMS / key)


def exits(fn, *a, **kw):
    with pytest.raises(SystemExit) as e:
        fn(*a, **kw)
    return e.value.code


def write_mapped(rel, text):
    p = shelf.MAPPED / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding=UTF)
    return p


def mapped_result(p):
    return next((e, w) for f, e, w in shelf.lint_mapped() if f == p)


# ---------------------------------------------------------------- norm

def test_norm_handles_ligatures_and_whitespace():
    assert shelf.norm("ﬁnal   \n QUOTE ") == shelf.norm("final quote")


def test_norm_handles_hyphenation_quotes_and_dashes():
    assert shelf.norm("atten-\ntion") == shelf.norm("attention")
    assert shelf.norm("self-\nattention") == shelf.norm("self-attention")
    assert shelf.norm("“so—called”") == shelf.norm('"so-called"')


# ---------------------------------------------------------------- L1 -> L2 -> L3

def test_add_moves_from_inbox_to_raw(lib):
    pdf = blank_pdf(lib / "inbox" / "tutorials" / "week 1.pdf")
    k = shelf.add(str(pdf), key="tut-week01")
    m = meta(k)
    assert not pdf.exists() and (lib / "inbox" / "tutorials").is_dir()  # folder kept
    assert (lib / "raw" / "tut-week01.pdf").is_file()
    assert m["raw"] == ["raw/tut-week01.pdf"] and m["collection"] == "tutorials"
    assert m["status"] == "extracted" and m["pages_pdf"] == 2 and len(m["sha256"]) == 64
    entry = shelf.load_manifest()["tut-week01.pdf"]
    assert entry["from"] == "inbox/tutorials/week 1.pdf" and entry["item"] == k
    assert not (shelf.ITEMS / k / "source.pdf").exists()
    assert not list(shelf.ITEMS.glob(".tmp-*"))


def test_add_outside_inbox_copies_and_keeps_original(lib, tmp_path_factory):
    pdf = blank_pdf(tmp_path_factory.mktemp("elsewhere") / "b.pdf")
    k = shelf.add(str(pdf))
    assert k.startswith("doc-") and pdf.exists()
    assert meta(k)["collection"] is None


def test_duplicate_is_refused_and_left_in_inbox(lib):
    pdf = blank_pdf(lib / "inbox" / "a.pdf")
    copy = lib / "inbox" / "notes" / "a-again.pdf"
    copy.parent.mkdir()
    shutil.copy(pdf, copy)
    shelf.add(str(pdf), key="k")
    assert "duplicate" in str(exits(shelf.add, str(copy), key="other"))
    assert copy.exists()
    assert shelf.add(str(copy), key="k", force=True) == "k-2"


def test_crash_leaves_inbox_untouched(lib, monkeypatch):
    pdf = blank_pdf(lib / "inbox" / "a.pdf")
    monkeypatch.setattr(shelf, "save_meta", lambda d, m: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        shelf.add(str(pdf), key="k")
    assert pdf.exists() and not (lib / "raw" / "k.pdf").exists()
    assert not (lib / "items" / "k").exists() and shelf.load_manifest() == {}


def test_readd_from_raw_after_deleting_item(lib):
    k = shelf.add(str(blank_pdf(lib / "inbox" / "a.pdf")), key="k")
    shutil.rmtree(shelf.ITEMS / k)
    assert shelf.add(str(lib / "raw" / "k.pdf"), key="k") == "k"
    assert shelf.load_manifest()["k.pdf"]["from"] == "inbox/a.pdf"  # history kept


def test_add_reads_printed_page_labels(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3, labels=True)), key="x")
    printed = [shelf.read_page(shelf.page_path(k, i))[0]["printed_page"] for i in (1, 2, 3)]
    assert printed == ["i", 1, 2]


def test_add_page_offset(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3)), key="x", page_offset=1)
    printed = [shelf.read_page(shelf.page_path(k, i))[0]["printed_page"] for i in (1, 2, 3)]
    assert printed == [None, 1, 2]


def test_add_scanned_pdf_is_pending(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="x", source="scan")
    assert meta(k)["status"] == "pending"
    assert shelf.TODO in shelf.read_page(shelf.page_path(k, 1))[1]


def test_add_converted_from_keeps_office_original(lib):
    pptx = lib / "inbox" / "slides" / "lec1.pptx"
    pdf = blank_pdf(lib / "inbox" / "slides" / "lec1.pdf")
    pptx.write_bytes(b"pptx bytes")
    k = shelf.add(str(pdf), key="lec01", converted_from=str(pptx), type="slides")
    assert meta(k)["converted_from"] == "raw/lec01.pptx" and not pptx.exists()
    assert (lib / "raw" / "lec01.pptx").read_bytes() == b"pptx bytes"


def test_add_rejects_non_pdf(lib):
    (lib / "a.jpg").write_bytes(b"x")
    assert "add-scan" in str(exits(shelf.add, str(lib / "a.jpg")))


# ---------------------------------------------------------------- scans

def test_add_scan_keeps_given_order(lib):
    d = lib / "inbox" / "handwritten"
    d.mkdir()
    imgs = []
    for name in ("IMG_9.jpg", "IMG_10.jpg"):  # page order chosen by the agent
        imgs.append(d / name)
        imgs[-1].write_bytes(name.encode())
    k = shelf.add_scan([str(p) for p in imgs], "Thermo L3")
    m = meta(k)
    assert k == "thermo-l3" and m["status"] == "pending" and m["type"] == "notes"
    assert m["raw"] == ["raw/thermo-l3-p001.jpg", "raw/thermo-l3-p002.jpg"]
    assert (lib / "raw" / "thermo-l3-p001.jpg").read_bytes() == b"IMG_9.jpg"
    assert m["collection"] == "handwritten" and not any(p.exists() for p in imgs)
    fm, body = shelf.read_page(shelf.page_path(k, 1))
    assert fm["source"] == "agent-transcription" and "../../../raw/thermo-l3-p001.jpg" in body
    assert shelf.lint_item(shelf.ITEMS / k)[0] == []
    m["status"] = "extracted"
    shelf.save_meta(shelf.ITEMS / k, m)
    assert any("shelf:todo" in e for e in shelf.lint_item(shelf.ITEMS / k)[0])


@pytest.mark.skipif(not shutil.which("pdftoppm"), reason="needs pdftoppm")
def test_render(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="x", source="scan")
    shelf.render(k, dpi=20)
    assert (shelf.ITEMS / k / "figures" / "p0002-full.png").is_file()
    assert shelf.read_page(shelf.page_path(k, 2))[1].startswith(
        "![page 2](../figures/p0002-full.png)")
    assert shelf.lint_item(shelf.ITEMS / k)[0] == []


# ---------------------------------------------------------------- verify / find

def test_verify_happy_sad_and_misaligned(lib, capsys):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    set_body(k, 2, "the quick brown fox\n")
    assert exits(shelf.verify, k, 2, "quick brown") == 0
    assert "OK" in capsys.readouterr().out
    assert exits(shelf.verify, k, 1, "quick brown") == 1
    assert "found on pdf page(s) [2]" in capsys.readouterr().out
    assert exits(shelf.verify, k, 2, "not in the text") == 1


def test_verify_ignores_frontmatter(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    assert exits(shelf.verify, k, 1, "pdf_page") == 1


def test_verify_mark_requires_clean_item(lib, capsys):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k", type="book")
    set_body(k, 1, "some real text\n")
    assert exits(shelf.verify, k, 1, "real text", mark=True) == 1
    assert "authors/year" in capsys.readouterr().out
    m = meta(k); m.update(authors=["A, B"], year=2020); shelf.save_meta(shelf.ITEMS / k, m)
    (shelf.ITEMS / k / "toc.md").write_text(
        "| Section | pdf pages |\n|---|---|\n| 1 | 1-2 |\n", encoding=UTF)
    assert exits(shelf.verify, k, 1, "real text", mark=True) == 0
    assert meta(k)["status"] == "verified"


def test_find_prints_citations(lib, capsys):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3, labels=True)), key="k")
    set_body(k, 3, "Scaled dot-product attention\n")
    shelf.find("scaled dot product attention")
    assert "[k · p.2]  items/k/pages/0003.md" in capsys.readouterr().out
    assert exits(shelf.find, "nowhere to be seen") == 1


# ---------------------------------------------------------------- lint items

def test_lint_fresh_item_has_no_errors(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    errs, warns = shelf.lint_item(shelf.ITEMS / k)
    assert errs == [] and any("toc.md" in w for w in warns)


def test_lint_catches_breakage(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3)), key="k")
    d = shelf.ITEMS / k
    (d / "pages" / "0002.md").unlink()
    set_body(k, 1, "![fig](../figures/missing.png)\n")
    with open(lib / "raw" / "k.pdf", "ab") as f:
        f.write(b"tampered")
    text = "\n".join(shelf.lint_item(d)[0])
    assert "no gaps" in text and "pages_pdf" in text
    assert "broken image" in text and "sha256" in text


def test_lint_missing_raw_is_only_a_warning(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    (lib / "raw" / "k.pdf").unlink()  # e.g. a git clone: raw/ is not committed
    errs, warns = shelf.lint_item(shelf.ITEMS / k)
    assert errs == [] and any("not present" in w for w in warns)


def test_lint_accepts_any_tool_but_not_empty_source(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    p = shelf.page_path(k, 1)
    fm, body = shelf.read_page(p)
    shelf.write_page(p, {**fm, "source": "olmocr@0.3"}, body)
    assert shelf.lint_item(shelf.ITEMS / k)[0] == []
    shelf.write_page(p, {**fm, "source": None}, body)
    assert any("source is empty" in e for e in shelf.lint_item(shelf.ITEMS / k)[0])


def test_lint_skips_example_items(lib):
    (shelf.ITEMS / "_example").mkdir()
    assert shelf.items() == []


# ---------------------------------------------------------------- L4 mapped/

def test_lint_mapped_citations(lib):
    shelf.add(str(blank_pdf(lib / "b.pdf", n=3, labels=True)), key="k")
    good = write_mapped("01-unit/01-topic.md",
                        "---\ntitle: Topic\nsources: [k]\n---\n"
                        "> Definition [k · p.1] and [k · pdf p.3].\n")
    assert mapped_result(good) == ([], [])
    bad = write_mapped("01-unit/02-bad.md",
                       "---\ntitle: Bad\nsources: [k, ghost]\n---\n"
                       "[k · pdf p.9] [k · p.77] [ghost · p.1]\n")
    text = "\n".join(mapped_result(bad)[0])
    assert "no item 'ghost'" in text and "pdf p.9] does not resolve" in text
    assert "p.77] does not resolve" in text and "[ghost" in text
    loose = write_mapped("loose.md", "no frontmatter, cites [k · pdf p.1]\n")
    errs, warns = mapped_result(loose)
    assert any("frontmatter" in e for e in errs)
    assert any("missing from sources" in w for w in warns)


def test_init_writes_mapped_templates(lib):
    for name in ("README.md", "subject.md", "log.md"):
        assert (shelf.MAPPED / name).is_file()
    _, warns = mapped_result(shelf.MAPPED / "subject.md")
    assert any("not filled in" in w for w in warns)
    assert mapped_result(shelf.MAPPED / "README.md")[0] == []  # `[key · p.N]` is an example


# ---------------------------------------------------------------- mineru / toc / rekey

def test_import_mineru(lib, tmp_path_factory):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    out = tmp_path_factory.mktemp("mineru")
    (out / "images").mkdir()
    (out / "images" / "abc.jpg").write_bytes(b"jpg")
    blocks = [
        {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Body text.", "page_idx": 0},
        {"type": "equation", "text": "$$a^2+b^2=c^2$$", "page_idx": 1},
        {"type": "image", "img_path": "images/abc.jpg", "img_caption": ["Figure 1: x"],
         "page_idx": 1},
    ]
    (out / "k_content_list.json").write_text(json.dumps(blocks), encoding=UTF)
    shelf.import_mineru(k, out / "k_content_list.json")
    fm1, b1 = shelf.read_page(shelf.page_path(k, 1))
    fm2, b2 = shelf.read_page(shelf.page_path(k, 2))
    assert fm1["source"] == "mineru" and b1.startswith("# Introduction\n\nBody text.")
    assert "$$a^2+b^2=c^2$$" in b2 and "](../figures/p0002-1.jpg)" in b2 and "Figure 1" in b2
    assert shelf.lint_item(shelf.ITEMS / k)[0] == []


def test_apply_toc_prefers_narrowest(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=4)), key="k")
    (shelf.ITEMS / k / "toc.md").write_text(
        "| Section | pdf pages |\n|---|---|\n| Ch 1 | 1–4 |\n"
        "| 1.1 Intro | 1–2 |\n| 1.2 Method | 2-3 |\n", encoding=UTF)
    shelf.apply_toc(k)
    secs = [shelf.read_page(shelf.page_path(k, i))[0]["section"] for i in range(1, 5)]
    assert secs == ["1.1 Intro", "1.1 Intro", "1.2 Method", "Ch 1"]


def test_rekey_updates_manifest_and_mapped(lib):
    k = shelf.add(str(blank_pdf(lib / "inbox" / "b.pdf")))
    topic = write_mapped("t.md", f"---\ntitle: T\nsources: [{k}]\n---\n[{k} · pdf p.1]\n")
    shelf.rekey(k, "smith2020-new-key")
    d = shelf.ITEMS / "smith2020-new-key"
    assert not (shelf.ITEMS / k).exists() and meta(d.name)["key"] == d.name
    assert shelf.read_page(d / "pages" / "0001.md")[0]["key"] == d.name
    assert shelf.load_manifest()[f"{k}.pdf"]["item"] == d.name  # raw name unchanged
    assert shelf.lint_item(d)[0] == []
    assert "[smith2020-new-key · pdf p.1]" in topic.read_text(encoding=UTF)
    assert mapped_result(topic) == ([], [])


# ---------------------------------------------------------------- catalog / bib / doctor / cli

def test_catalog_and_bib(lib, capsys):
    shelf.catalog()
    assert "No items yet" in (lib / "catalog.md").read_text(encoding=UTF)
    k = shelf.add(str(blank_pdf(lib / "inbox" / "books" / "b.pdf")), key="k",
                  title="A | B", authors=["Doe, Jane"], year=2001, type="book")
    shelf.catalog()
    assert "| `k` | A \\| B | Doe, Jane | 2001 | book | books | 2 | extracted |" in \
        (lib / "catalog.md").read_text(encoding=UTF)
    capsys.readouterr()
    shelf.bib([k])
    out = capsys.readouterr().out
    assert out.startswith("@book{k,") and "author = {Doe, Jane}" in out


def test_doctor_reports_inbox(lib, capsys):
    blank_pdf(lib / "inbox" / "a.pdf")
    shelf.add(str(blank_pdf(lib / "inbox" / "x.pdf")), key="x")
    pyqs = lib / "inbox" / "pyqs"
    pyqs.mkdir()
    shutil.copy(lib / "raw" / "x.pdf", pyqs / "x-copy.pdf")
    (pyqs / "q1.png").write_bytes(b"png")
    (pyqs / "notes.docx").write_bytes(b"docx")
    shelf.doctor()
    out = capsys.readouterr().out
    assert "inbox/: 4 file(s) waiting" in out and "a.pdf: pdf" in out
    assert "pyqs/x-copy.pdf: DUPLICATE of raw/x.pdf" in out
    assert "pyqs/q1.png: image" in out and "pyqs/notes.docx: office file" in out
    assert "subject.md not filled in" in out


def test_cli_help_and_bad_command():
    r = subprocess.run([sys.executable, str(SHELF), "--help"], capture_output=True)
    assert r.returncode == 0 and b"verify" in r.stdout
    r = subprocess.run([sys.executable, str(SHELF), "frobnicate"], capture_output=True)
    assert r.returncode != 0  # clean error, not a traceback
    r = subprocess.run([sys.executable, str(SHELF), "page", "nope", "1"], capture_output=True)
    assert r.returncode == 1 and b"Traceback" not in r.stderr


# ---------------------------------------------------------------- logs

def test_add_writes_conversion_log(lib):
    k = shelf.add(str(blank_pdf(lib / "inbox" / "Sem 3" / "tut 1.pdf")), key="tut01",
                  source="scan")
    log = (shelf.ITEMS / k / "log.md").read_text(encoding=UTF)
    assert log.startswith("# Conversion log: tut01")
    assert "raw/tut01.pdf (from inbox/Sem 3/tut 1.pdf)" in log
    assert "marked for OCR/transcription" in log and ".tmp-" not in log
    assert meta(k)["collection"] == "Sem 3"


def test_unclear_text_must_be_explained_in_log(lib):
    img = lib / "inbox" / "IMG_1.jpg"
    img.write_bytes(b"img")
    k = shelf.add_scan([str(img)], "notes-a")
    d = shelf.ITEMS / k
    set_body(k, 1, "F = [unclear: ma or mg] and [illegible]\n")
    assert any("p0001 but log.md doesn't explain" in w for w in shelf.lint_item(d)[1])
    shelf.log_append(d, "agent-transcription", [
        "p0001: bottom line smudged; read as 'ma', could be 'mg'",
        "[ ] Is the formula on p0001 F = ma or F = mg?"])
    warns = shelf.lint_item(d)[1]
    assert not any("doesn't explain" in w for w in warns)
    assert any("1 open question" in w for w in warns)


def test_doctor_lists_open_questions(lib, capsys):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    shelf.log_append(shelf.ITEMS / k, "agent", ["[x] answered -> yes"])
    with open(shelf.ITEMS / k / "log.md", "a", encoding=UTF) as f:
        f.write("- [ ] Which year is this paper from?\n")
    with open(shelf.MAPPED / "log.md", "a", encoding=UTF) as f:
        f.write("\n## run 1\n- [ ] Should waves go before optics?\n- [x] old -> done\n")
    shelf.doctor()
    out = capsys.readouterr().out
    assert "open questions for the user: 2" in out
    assert "items/k/log.md: Which year is this paper from?" in out
    assert "mapped/log.md: Should waves go before optics?" in out


def test_rekey_and_import_append_to_log(lib, tmp_path_factory):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="old")
    out = tmp_path_factory.mktemp("m")
    (out / "c.json").write_text(json.dumps([{"type": "text", "text": "x", "page_idx": 0}]),
                                encoding=UTF)
    shelf.import_mineru(k, out / "c.json")
    shelf.rekey(k, "new")
    log = (shelf.ITEMS / "new" / "log.md").read_text(encoding=UTF)
    assert "shelf.py import-mineru" in log and "nothing on pages [2]" in log
    assert "renamed from old" in log
