import json, subprocess, sys
from pathlib import Path

import pytest
import yaml
from pypdf import PdfWriter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shelf  # noqa: E402

SHELF = Path(__file__).resolve().parents[1] / "shelf.py"


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """An empty library rooted in tmp_path."""
    monkeypatch.setattr(shelf, "ROOT", tmp_path)
    monkeypatch.setattr(shelf, "ITEMS", tmp_path / "items")
    monkeypatch.setattr(shelf, "INBOX", tmp_path / "inbox")
    shelf.init()
    return tmp_path


def blank_pdf(path, n=2, labels=False):
    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(612, 792)
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


# ---------------------------------------------------------------- norm

def test_norm_handles_ligatures_and_whitespace():
    assert shelf.norm("ﬁnal   \n QUOTE ") == shelf.norm("final quote")


def test_norm_handles_hyphenation_quotes_and_dashes():
    assert shelf.norm("atten-\ntion") == shelf.norm("attention")
    assert shelf.norm("self-\nattention") == shelf.norm("self-attention")
    assert shelf.norm("“so—called”") == shelf.norm('"so-called"')


# ---------------------------------------------------------------- add

def test_add_blank_pdf(lib):
    shelf.add(str(blank_pdf(lib / "b.pdf")))
    key = next(shelf.ITEMS.iterdir()).name
    assert key.startswith("doc-")
    assert len(list((shelf.ITEMS / key / "pages").glob("*.md"))) == 2
    m = meta(key)
    assert m["status"] == "extracted" and m["pages_pdf"] == 2
    assert len(m["sha256"]) == 64 and m["added"] != "YYYY-MM-DD"
    assert not list(shelf.ITEMS.glob(".tmp-*"))


def test_add_explicit_key_metadata_and_collision(lib):
    pdf = blank_pdf(lib / "b.pdf")
    k = shelf.add(str(pdf), key="Vaswani2017 Attention", title="T",
                  authors=["Vaswani, Ashish"], year=2017, type="paper")
    assert k == "vaswani2017-attention"
    assert meta(k)["authors"] == ["Vaswani, Ashish"] and meta(k)["year"] == 2017
    assert exits(shelf.add, str(pdf), key=k) != 0          # same sha256 refused
    assert shelf.add(str(pdf), key=k, force=True) == f"{k}-2"


def test_add_reads_printed_page_labels(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3, labels=True)), key="x")
    printed = [shelf.read_page(shelf.page_path(k, i))[0]["printed_page"] for i in (1, 2, 3)]
    assert printed == ["i", 1, 2]


def test_add_page_offset(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3)), key="x", page_offset=1)
    printed = [shelf.read_page(shelf.page_path(k, i))[0]["printed_page"] for i in (1, 2, 3)]
    assert printed == [None, 1, 2]


def test_add_rejects_non_pdf(lib):
    (lib / "a.jpg").write_bytes(b"x")
    assert "add-scan" in str(exits(shelf.add, str(lib / "a.jpg")))


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
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    set_body(k, 1, "some real text\n")
    assert exits(shelf.verify, k, 1, "real text", mark=True) == 1
    assert "authors/year" in capsys.readouterr().out
    m = meta(k); m.update(authors=["A, B"], year=2020); shelf.save_meta(shelf.ITEMS / k, m)
    (shelf.ITEMS / k / "toc.md").write_text("| Section | pdf pages |\n|---|---|\n| 1 | 1-2 |\n")
    assert exits(shelf.verify, k, 1, "real text", mark=True) == 0
    assert meta(k)["status"] == "verified"


def test_find_prints_citations(lib, capsys):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3, labels=True)), key="k")
    set_body(k, 3, "Scaled dot-product attention\n")
    shelf.find("scaled dot product attention")
    assert "[k · p.2]  items/k/pages/0003.md" in capsys.readouterr().out
    assert exits(shelf.find, "nowhere to be seen") == 1


# ---------------------------------------------------------------- lint

def test_lint_fresh_item_has_no_errors(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    errs, warns = shelf.lint_item(shelf.ITEMS / k)
    assert errs == []
    assert any("toc.md" in w for w in warns)


def test_lint_catches_breakage(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=3)), key="k")
    d = shelf.ITEMS / k
    (d / "pages" / "0002.md").unlink()
    set_body(k, 1, "![fig](../figures/missing.png)\n")
    with open(d / "source.pdf", "ab") as f:
        f.write(b"tampered")
    errs, _ = shelf.lint_item(d)
    text = "\n".join(errs)
    assert "no gaps" in text and "pages_pdf" in text
    assert "broken image" in text and "sha256" in text


def test_lint_skips_example_items(lib):
    (shelf.ITEMS / "_example").mkdir()
    assert shelf.items() == []


# ---------------------------------------------------------------- scans

def test_add_scan(lib):
    imgs = []
    for i in (2, 1):
        imgs.append(lib / "inbox" / f"notes-p00{i}.jpg")
        imgs[-1].write_bytes(f"img{i}".encode())
    k = shelf.add_scan([str(p) for p in imgs], "My Notes")
    d = shelf.ITEMS / k
    assert k == "my-notes" and meta(k)["status"] == "pending" and meta(k)["type"] == "notes"
    assert (d / "figures" / "p0001-full.jpg").read_bytes() == b"img1"
    fm, body = shelf.read_page(d / "pages" / "0001.md")
    assert fm["source"] == "agent-transcription" and "../figures/p0001-full.jpg" in body
    assert shelf.lint_item(d)[0] == []
    m = meta(k); m["status"] = "extracted"; shelf.save_meta(d, m)
    assert any("shelf:todo" in e for e in shelf.lint_item(d)[0])


# ---------------------------------------------------------------- mineru / toc / rekey

def test_import_mineru(lib, tmp_path):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k")
    out = tmp_path / "mineru"
    (out / "images").mkdir(parents=True)
    (out / "images" / "abc.jpg").write_bytes(b"jpg")
    blocks = [
        {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
        {"type": "text", "text": "Body text.", "page_idx": 0},
        {"type": "equation", "text": "$$a^2+b^2=c^2$$", "page_idx": 1},
        {"type": "image", "img_path": "images/abc.jpg", "img_caption": ["Figure 1: x"],
         "page_idx": 1},
    ]
    (out / "k_content_list.json").write_text(json.dumps(blocks))
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
        "| 1.1 Intro | 1–2 |\n| 1.2 Method | 2-3 |\n")
    shelf.apply_toc(k)
    secs = [shelf.read_page(shelf.page_path(k, i))[0]["section"] for i in range(1, 5)]
    assert secs == ["1.1 Intro", "1.1 Intro", "1.2 Method", "Ch 1"]


def test_rekey(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")))
    shelf.rekey(k, "smith2020-new-key")
    d = shelf.ITEMS / "smith2020-new-key"
    assert not (shelf.ITEMS / k).exists() and meta(d.name)["key"] == d.name
    assert shelf.read_page(d / "pages" / "0001.md")[0]["key"] == d.name
    assert shelf.lint_item(d)[0] == []


# ---------------------------------------------------------------- catalog / bib / cli

def test_catalog_and_bib(lib, capsys):
    shelf.catalog()
    assert "No items yet" in (lib / "catalog.md").read_text()
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="k", title="A | B",
                  authors=["Doe, Jane"], year=2001, type="book")
    shelf.catalog()
    assert "| `k` | A \\| B | Doe, Jane | 2001 | book | 2 | extracted |" in \
        (lib / "catalog.md").read_text()
    capsys.readouterr()
    shelf.bib([k])
    out = capsys.readouterr().out
    assert out.startswith("@book{k,") and "author = {Doe, Jane}" in out


def test_cli_help_and_bad_command():
    r = subprocess.run([sys.executable, str(SHELF), "--help"], capture_output=True)
    assert r.returncode == 0 and b"verify" in r.stdout
    r = subprocess.run([sys.executable, str(SHELF), "frobnicate"], capture_output=True)
    assert r.returncode != 0  # clean error, not a traceback
    r = subprocess.run([sys.executable, str(SHELF), "page", "nope", "1"], capture_output=True)
    assert r.returncode == 1 and b"Traceback" not in r.stderr
