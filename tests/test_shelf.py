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
    shutil.copy(SHELF.parent / "tools.registry.json", tmp_path)
    monkeypatch.setenv("SHELF_HOME", str(tmp_path / "shelf-home"))  # never the real ~/.shelf
    monkeypatch.setattr(shelf, "extra_bin_dirs", lambda: [])   # nor real user installs
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


def test_rekey_appends_to_log(lib):
    k = shelf.add(str(blank_pdf(lib / "b.pdf")), key="old")
    shelf.rekey(k, "new")
    log = (shelf.ITEMS / "new" / "log.md").read_text(encoding=UTF)
    assert "shelf.py add" in log and "renamed from old" in log


# ---------------------------------------------------------------- tools

REG = {"updated": "2026-10-22", "categories": {
    "pdf-text": {"job": "j", "fallback": None, "tools": [
        {"name": "heavy", "min_tier": "high", "detect": ["heavy-bin"], "why": "w", "url": "u",
         "install": {"pip": "heavy[all]"}},
        {"name": "medium", "min_tier": "mid", "detect": ["medium-bin"], "why": "w", "url": "u",
         "install": {"pip": "medium"}},
        {"name": "builtin", "min_tier": "low", "builtin": True, "detect": [], "why": "w",
         "url": "u", "install": {}}]},
    "render": {"job": "j", "fallback": None, "tools": [
        {"name": "best", "min_tier": "low", "detect": ["best-bin"], "why": "w", "url": "u",
         "install": {"apt": "best-pkg", "brew": "best"}},
        {"name": "okay", "min_tier": "low", "detect": ["okay-bin", "okay-alt"], "why": "w",
         "url": "u", "install": {"apt": "okay-pkg"}}]},
    "office": {"job": "j", "fallback": "ask", "tools": [
        {"name": "soff", "min_tier": "low", "detect": ["soff-bin"], "why": "w", "url": "u",
         "install": {}}]}}}


@pytest.fixture
def machine(lib, monkeypatch):
    """Made-up registry and hardware, a PATH we control, and no real installs."""
    (lib / "tools.registry.json").write_text(json.dumps(REG), encoding=UTF)
    state = {"path": {"apt-get"}, "tier": "mid", "ran": [], "tty": False}
    monkeypatch.setattr(shelf.shutil, "which",
                        lambda c: f"/bin/{c}" if c in state["path"] else None)
    monkeypatch.setattr(shelf, "platform_key", lambda: "linux")
    monkeypatch.setattr(shelf, "externally_managed", lambda: True)
    monkeypatch.setattr(shelf, "hardware", lambda: {
        "os": "linux", "ram_gb": 16, "cpus": 8, "gpu": None, "vram_gb": 0,
        "tier": state["tier"]})

    def fake_run(cmd, shell):
        state["ran"].append(cmd)
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(shelf.subprocess, "run", fake_run)
    monkeypatch.setattr(shelf.sys.stdin, "isatty", lambda: state["tty"], raising=False)
    return state


def test_registry_file_is_complete():
    reg = json.loads((SHELF.parent / "tools.registry.json").read_text(encoding=UTF))
    assert reg["updated"] and reg["categories"]
    for cat in reg["categories"].values():
        assert cat["job"] and cat["tools"]
        for tool in cat["tools"]:
            assert tool["name"] and tool["why"] and tool["url"]
            assert tool["min_tier"] in shelf.TIERS
            assert tool["detect"] or tool.get("builtin")
            assert set(tool["install"]) <= {"apt", "dnf", "pacman", "brew", "winget",
                                            "conda", "pip"}


@pytest.mark.parametrize("ram,vram,apple,tier", [
    (4, 0, False, "low"), (7.6, 0, False, "mid"), (15, 4, False, "mid"),
    (15, 8, False, "high"), (8, 0, True, "mid"), (16, 0, True, "high")])
def test_hardware_tier(monkeypatch, ram, vram, apple, tier):
    monkeypatch.setattr(shelf, "ram_gb", lambda: ram)
    monkeypatch.setattr(shelf.shutil, "which", lambda c: "/bin/x" if c == "nvidia-smi" and vram else None)
    monkeypatch.setattr(shelf.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, f"Some GPU, {int(vram * 1024)}\n", ""))
    monkeypatch.setattr(shelf.sys, "platform", "darwin" if apple else "linux")
    import platform
    monkeypatch.setattr(platform, "machine", lambda: "arm64" if apple else "x86_64")
    assert shelf.hardware()["tier"] == tier


def test_choose_is_deterministic_and_tier_aware(machine):
    reg = shelf.load_registry()
    pick = lambda tier: {c: tuple(t and t["name"] for t in p)
                         for c, p in shelf.choose(reg, tier).items()}
    assert pick("high")["pdf-text"] == ("heavy", "builtin")
    assert pick("mid")["pdf-text"] == ("medium", "builtin")
    assert pick("low")["pdf-text"] == ("builtin", None)
    assert pick("mid") == pick("mid")
    assert pick("mid")["render"] == ("best", None)       # installable via apt
    machine["path"].add("okay-alt")
    assert pick("mid")["render"] == ("best", "okay")     # backup = installed only
    machine["path"].discard("apt-get")
    assert pick("mid")["render"] == ("okay", None)       # best can't be had here
    assert pick("mid")["office"] == (None, None)


def test_setup_batches_admin_installs_for_the_user(machine, lib, capsys):
    shelf.setup()
    out = capsys.readouterr().out
    assert machine["ran"] == []    # no tty: nothing needing sudo is run
    assert "sudo apt-get install -y pipx best-pkg" in out
    assert "again for: medium" in out
    t = json.loads(shelf.tools_path().read_text(encoding=UTF))
    assert t["machine"]["tier"] == "mid"
    assert t["pending_admin"] == "sudo apt-get install -y pipx best-pkg"
    assert t["categories"]["pdf-text"] == {"job": "j", "use": "medium", "backup": "builtin",
                                           "fallback": None, "ready": False, "path": None}
    assert shelf.effective(t, "pdf-text") == "builtin"   # until medium is installed


def test_setup_installs_user_level_tools_without_asking(machine, lib):
    machine["path"] |= {"pipx", "best-bin"}
    shelf.setup()
    assert machine["ran"] == ['pipx install "medium"']
    machine["tty"] = True
    machine["path"].discard("best-bin")
    machine["ran"].clear()
    shelf.setup()   # a person at a terminal: sudo runs (and asks for the password)
    assert machine["ran"] == ['pipx install "medium"', "sudo apt-get install -y best-pkg"]


def test_setup_installs_a_shared_tool_once(machine, lib):
    reg = json.loads((lib / "tools.registry.json").read_text(encoding=UTF))
    reg["categories"]["scan-ocr"] = {"job": "j", "fallback": None,
                                     "tools": [reg["categories"]["pdf-text"]["tools"][1]]}
    (lib / "tools.registry.json").write_text(json.dumps(reg), encoding=UTF)
    machine["path"].add("pipx")
    shelf.setup()
    assert machine["ran"] == ['pipx install "medium"']


def test_setup_dry_run_changes_nothing(machine, lib, capsys):
    shelf.setup(dry_run=True)
    assert machine["ran"] == [] and not shelf.tools_path().exists()
    assert "dry run" in capsys.readouterr().out


def test_tools_refreshes_ready_and_honours_overrides(machine, lib, capsys):
    assert "run `python3 shelf.py setup`" in str(exits(shelf.tools))
    shelf.setup()
    machine["path"].add("medium-bin")      # the user ran the admin command + setup
    shelf.tools()
    t = json.loads(shelf.tools_path().read_text(encoding=UTF))
    assert t["categories"]["pdf-text"]["ready"] and shelf.effective(t, "pdf-text") == "medium"
    t["overrides"]["pdf-text"] = {"use": "mytool", "how": "mytool {pdf}"}
    shelf.tools_path().write_text(json.dumps(t), encoding=UTF)
    shelf.setup()
    t = json.loads(shelf.tools_path().read_text(encoding=UTF))
    assert t["overrides"]["pdf-text"]["use"] == "mytool"
    assert shelf.effective(t, "pdf-text") == "mytool"


def test_add_scan_heic_keeps_original_and_links_jpg(lib, monkeypatch):
    heic = lib / "inbox" / "notes" / "IMG_1.HEIC"
    heic.parent.mkdir(parents=True)
    heic.write_bytes(b"heic bytes")
    real_which = shutil.which
    monkeypatch.setattr(shelf.shutil, "which",
                        lambda c: None if c in ("magick", "heif-convert", "heif-dec")
                        else real_which(c))
    assert "imagemagick" in str(exits(shelf.add_scan, [str(heic)], "n"))
    assert heic.exists()  # nothing moved when it can't proceed
    monkeypatch.setattr(shelf.shutil, "which",
                        lambda c: "/bin/heif-convert" if c == "heif-convert" else real_which(c))

    def fake_convert(cmd, capture_output):
        Path(cmd[2]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(shelf.subprocess, "run",
                        lambda cmd, capture_output: subprocess.CompletedProcess(cmd, 1, b"", b"bad file"))
    assert "could not convert IMG_1.HEIC" in str(exits(shelf.add_scan, [str(heic)], "n"))
    assert heic.exists() and not list((lib / "raw").glob("*.heic"))  # rolled back
    monkeypatch.setattr(shelf.subprocess, "run", fake_convert)
    k = shelf.add_scan([str(heic)], "notes-heic")
    d = shelf.ITEMS / k
    assert (lib / "raw" / "notes-heic-p001.heic").read_bytes() == b"heic bytes"
    assert (d / "figures" / "p0001-full.jpg").is_file() and not heic.exists()
    assert "](../figures/p0001-full.jpg)" in shelf.read_page(shelf.page_path(k, 1))[1]
    assert "HEIC" in (d / "log.md").read_text(encoding=UTF)
    assert shelf.lint_item(d)[0] == []


def test_picks_are_per_computer_and_kept(machine, lib, tmp_path_factory, monkeypatch):
    shelf.setup()
    assert shelf.tools_path().parent.name == "shelf-home"   # not inside the library
    assert shelf.tools_json()["categories"]["pdf-text"]["use"] == "medium"
    machine["tier"] = "high"            # e.g. a newer registry or more RAM later
    other = tmp_path_factory.mktemp("second-project")
    monkeypatch.setattr(shelf, "ROOT", other)
    shutil.copy(lib / "tools.registry.json", other)
    shelf.setup()                       # second project, same computer: same tools
    assert shelf.tools_json()["categories"]["pdf-text"]["use"] == "medium"
    shelf.setup(refresh=True)
    assert shelf.tools_json()["categories"]["pdf-text"]["use"] == "heavy"


# ---------------------------------------------------------------- extract

def scanned_item(lib, n=2):
    k = shelf.add(str(blank_pdf(lib / "inbox" / "s.pdf", n=n)), key="scan", source="scan")
    return k


def test_extract_refuses_images_and_unknown_tools(lib):
    (lib / "inbox" / "n.jpg").write_bytes(b"x")
    k = shelf.add_scan([str(lib / "inbox" / "n.jpg")], "notes")
    assert "transcribes those itself" in str(exits(shelf.extract, k))
    k = scanned_item(lib)
    assert "no OCR tool" in str(exits(shelf.extract, k))   # no setup on this computer
    assert "can't run" in str(exits(shelf.extract, k, tool="magic"))


def test_extract_with_tesseract_fills_pages(lib, monkeypatch):
    k = scanned_item(lib)
    real_which = shutil.which
    monkeypatch.setattr(shelf.shutil, "which",
                        lambda c: "/bin/tesseract" if c == "tesseract" else real_which(c))
    monkeypatch.setattr(shelf, "render_pages", lambda pdf, out, dpi: {
        1: out / "p-1.png", 2: out / "p-2.png"})
    monkeypatch.setattr(shelf.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, "The first law\n" if cmd[1].endswith("p-1.png") else "", ""))
    shelf.extract(k, tool="tesseract")
    fm, body = shelf.read_page(shelf.page_path(k, 1))
    assert fm["source"] == "tesseract" and body.strip() == "The first law"
    assert shelf.TODO in shelf.read_page(shelf.page_path(k, 2))[1]   # nothing found
    assert meta(k)["status"] == "pending"
    log = (shelf.ITEMS / k / "log.md").read_text(encoding=UTF)
    assert "extract (tesseract)" in log and "found nothing on pages [2]" in log


def test_extract_keeps_page_images_and_uses_computer_pick(lib, monkeypatch):
    k = scanned_item(lib, n=1)
    p = shelf.page_path(k, 1)
    fm, body = shelf.read_page(p)
    shelf.write_page(p, fm, "![page 1](../figures/p0001-full.png)\n\n" + body)
    (shelf.ITEMS / k / "figures" / "p0001-full.png").write_bytes(b"png")
    shelf.tools_path().parent.mkdir(parents=True)
    shelf.tools_path().write_text(json.dumps({"categories": {"scan-ocr": {
        "use": "tesseract", "ready": True, "backup": None}}, "overrides": {}}), encoding=UTF)
    real_which = shutil.which
    monkeypatch.setattr(shelf.shutil, "which",
                        lambda c: "/bin/tesseract" if c == "tesseract" else real_which(c))
    monkeypatch.setattr(shelf, "render_pages", lambda pdf, out, dpi: {1: out / "p-1.png"})
    monkeypatch.setattr(shelf.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, "Heat and work\n", ""))
    shelf.extract(k)
    body = shelf.read_page(p)[1]
    assert body == "![page 1](../figures/p0001-full.png)\n\nHeat and work\n"
    assert meta(k)["status"] == "extracted" and shelf.lint_item(shelf.ITEMS / k)[0] == []


class FakeMinerU:
    """Stands in for MinerU 4's CLI: a server that needs starting, and
    `parse <page image> -o out.md`."""
    def __init__(self, running=False, texts=None, fail=()):
        self.running, self.texts, self.fail, self.calls = running, texts or {}, fail, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd[1:3])
        ok = subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:3] == ["server", "status"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(
                {"running": self.running,
                 "parse_server": {"local": {"healthy": self.running}}}), "")
        if cmd[1:3] == ["server", "start"]:
            self.running = True
        elif cmd[1:3] == ["server", "stop"]:
            self.running = False
        elif cmd[1] == "parse":
            page = int(Path(cmd[2]).name[1:5])
            if page in self.fail:
                return subprocess.CompletedProcess(cmd, 1, "", "engine error")
            Path(cmd[cmd.index("-o") + 1]).write_text(self.texts.get(page, ""), encoding=UTF)
        return ok


def mineru_item(lib, monkeypatch, fake, n=3):
    k = shelf.add(str(blank_pdf(lib / "b.pdf", n=n)), key="paper")
    real_which = shutil.which
    monkeypatch.setattr(shelf.shutil, "which",
                        lambda c: "/bin/mineru" if c == "mineru" else real_which(c))
    monkeypatch.setattr(shelf.subprocess, "run", fake)

    def fake_render(pdf, out, dpi):
        files = {i: out / f"p-{i}.png" for i in range(1, n + 1)}
        for f in files.values():
            f.write_bytes(b"png")
        return files
    monkeypatch.setattr(shelf, "render_pages", fake_render)
    return k


def test_extract_mineru_page_by_page(lib, monkeypatch, capsys):
    fake = FakeMinerU(texts={1: "# Intro\n\n<span>x &lt; y</span>\n\n![image block](doc:abc)\n",
                             3: "Last page"})
    k = mineru_item(lib, monkeypatch, fake)
    shelf.extract(k, tool="mineru")
    fm, b1 = shelf.read_page(shelf.page_path(k, 1))
    assert fm["source"] == "mineru"
    assert b1 == ("# Intro\n\nx < y\n\n"
                  "![image (see page image)](../figures/p0001-full.png)\n")
    assert shelf.TODO in shelf.read_page(shelf.page_path(k, 2))[1]   # blank: nothing found
    assert meta(k)["status"] == "pending"                          # until p2 is done
    assert ["server", "start"] in fake.calls and ["server", "stop"] in fake.calls
    assert not fake.running                                       # we stopped what we started
    assert shelf.lint_item(shelf.ITEMS / k)[0] == []
    assert "found nothing on pages [2]" in (shelf.ITEMS / k / "log.md").read_text(encoding=UTF)


def test_extract_empty_result_keeps_earlier_text(lib, monkeypatch):
    fake = FakeMinerU(texts={1: "x"})
    k = mineru_item(lib, monkeypatch, fake, n=2)
    set_body(k, 2, "text pypdf found\n")
    shelf.extract(k, tool="mineru")
    fm, body = shelf.read_page(shelf.page_path(k, 2))
    assert body == "text pypdf found\n" and fm["source"] == "pypdf"
    assert meta(k)["status"] == "extracted"


def test_extract_mineru_leaves_users_server_and_resumes(lib, monkeypatch, capsys):
    fake = FakeMinerU(running=True, texts={1: "a", 2: "b", 3: "c"}, fail=(2,))
    k = mineru_item(lib, monkeypatch, fake)
    shelf.extract(k, tool="mineru")
    assert fake.running and ["server", "start"] not in fake.calls
    out = capsys.readouterr().out
    assert "FAILED on pages [2]" in out
    assert shelf.read_page(shelf.page_path(k, 2))[0]["source"] == "pypdf"   # earlier text kept
    fake.fail, fake.calls = (), []
    shelf.extract(k, tool="mineru")          # only the failed page is retried
    assert [c for c in fake.calls if c[0] == "parse"] == [["parse", str(
        shelf.ITEMS / k / "figures" / "p0002-full.png")]]
    log_before = (shelf.ITEMS / k / "log.md").read_text(encoding=UTF)
    shelf.extract(k, tool="mineru")          # nothing left: no run, no log entry
    assert "already came from mineru" in capsys.readouterr().out
    assert (shelf.ITEMS / k / "log.md").read_text(encoding=UTF) == log_before


def test_find_exe_checks_user_install_dirs_and_use_paths(lib, monkeypatch, tmp_path):
    bindir = tmp_path / "localbin"
    bindir.mkdir()
    exe = bindir / "mytool"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(shelf.shutil, "which", lambda c: None)
    assert shelf.find_exe("mytool") is None
    monkeypatch.setattr(shelf, "extra_bin_dirs", lambda: [bindir])
    assert shelf.find_exe("mytool") == str(exe)
    monkeypatch.setattr(shelf, "extra_bin_dirs", lambda: [])
    shelf.tools_path().parent.mkdir(parents=True)
    shelf.tools_path().write_text(json.dumps({"paths": {"mytool": str(exe)}}), encoding=UTF)
    assert shelf.find_exe("mytool") == str(exe)


def test_setup_use_records_a_path(machine, lib, tmp_path):
    exe = tmp_path / "venv-medium"
    exe.write_text("x")
    shelf.setup(use=[f"medium-bin={exe}"])
    t = shelf.tools_json()
    assert t["paths"]["medium-bin"] == str(exe.resolve())
    assert t["categories"]["pdf-text"]["use"] == "medium" and t["categories"]["pdf-text"]["ready"]
    assert machine["ran"] == []      # medium is found via the path, nothing to install
    assert "no such file" in str(exits(shelf.setup, use=["x=/nope"]))


def test_setup_prefers_uv_for_python_tools(machine, lib):
    machine["path"] |= {"uv", "pipx", "best-bin"}
    shelf.setup()
    assert machine["ran"] == ['uv tool install "medium"']


@pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("pdftoppm")),
                    reason="needs tesseract and pdftoppm")
def test_extract_real_tesseract_on_blank_scan(lib):
    k = scanned_item(lib, n=1)
    shelf.extract(k, tool="tesseract")
    assert shelf.read_page(shelf.page_path(k, 1))[0]["source"] == "tesseract"
