import json
import os
import shutil
import tempfile
import tkinter as tk
import unittest
from unittest import mock

from reference_doctor import app as A
from reference_doctor import appkit as ak
from reference_doctor import bib

GOOD = {"type": "article", "key": "lee2020", "author": "Lee, Ana and Park, Ji", "title": "A study of things", "journal": "J. Things", "year": "2020",
        "volume": "3", "number": "2", "pages": "10--20", "doi": "10.1000/abc"}


class TestParsing(unittest.TestCase):
    def test_bibtex_features(self):
        text = """@comment{skip}
@string{jt = "J. Things"}
@article{a1, title = {Nested {Braces} here}, author = "Lee, Ana", year = 2020,
  note = {multi
          line}}
@book(b2, title = "Round brackets", publisher = {P}, year = {2001})
"""
        es, problems = bib.parse_bibtex(text)
        self.assertEqual(len(es), 2)
        self.assertEqual(es[0]["title"], "Nested {Braces} here")
        self.assertEqual(es[0]["year"], "2020")
        self.assertEqual(es[0]["note"], "multi line")
        self.assertEqual((es[1]["type"], es[1]["key"]), ("book", "b2"))
        self.assertEqual(problems, [])

    def test_broken_file_reports_and_continues(self):
        es, problems = bib.parse_bibtex("@article{a, title = {Never closed\n@book{b, title={Fine}, year=2000}\n")
        self.assertTrue(problems)

    def test_ris_and_csl(self):
        es = bib.parse_ris("TY  - JOUR\nAU  - Lee, Ana\nAU  - Park, Ji\nTI  - T\nJO  - J\nPY  - 2020/01/02\nSP  - 1\nEP  - 9\nER  - \n")
        self.assertEqual((es[0]["author"], es[0]["year"], es[0]["pages"]), ("Lee, Ana and Park, Ji", "2020", "1--9"))
        csl = bib.parse_csl('[{"id":"x","type":"article-journal","title":"T","author":[{"family":"Lee","given":"Ana"}],"issued":{"date-parts":[[2020]]}}]')
        self.assertEqual((csl[0]["type"], csl[0]["author"], csl[0]["year"]), ("article", "Lee, Ana", "2020"))

    def test_detection(self):
        self.assertEqual(bib.parse_any("@misc{a, title={T}}")[2], "BibTeX")
        self.assertEqual(bib.parse_any("TY  - JOUR\nTI  - T\nER  - \n")[2], "RIS")
        self.assertEqual(bib.parse_any('[{"id":"a"}]')[2], "CSL JSON")
        with self.assertRaises(ValueError):
            bib.parse_any("just some words")


class TestRoundTrips(unittest.TestCase):
    def test_all_formats(self):
        self.assertEqual(bib.parse_bibtex(bib.to_bibtex([GOOD]))[0][0], GOOD)
        r = bib.parse_ris(bib.to_ris([GOOD]))[0]
        for k in ("title", "author", "year", "journal", "pages", "doi", "volume", "number"):
            self.assertEqual(r[k], GOOD[k], k)
        c = bib.parse_csl(bib.to_csl([GOOD]))[0]
        for k in ("title", "author", "year", "journal", "doi", "volume"):
            self.assertEqual(c[k], GOOD[k], k)

    def test_unknown_fields_survive(self):
        e = dict(GOOD, abstract="Keep me", keywords="a, b")
        self.assertEqual(bib.parse_bibtex(bib.to_bibtex([e]))[0][0]["abstract"], "Keep me")


class TestNormalising(unittest.TestCase):
    def test_authors(self):
        self.assertEqual(bib.normal_author("John Smith"), "Smith, John")
        self.assertEqual(bib.normal_author("Smith, John"), "Smith, John")
        self.assertEqual(bib.normal_author("Plato"), "Plato")
        self.assertEqual(bib.normal_author("Ana de Souza"), "de Souza, Ana")
        self.assertEqual(bib.normal_author("Jan van der Berg"), "van der Berg, Jan")
        self.assertEqual(bib.normal_author("others"), "others")

    def test_doi(self):
        self.assertEqual(bib.normal_doi("https://doi.org/10.1000/xyz."), "10.1000/xyz")
        self.assertEqual(bib.normal_doi("doi:10.1000/a(b)c"), "10.1000/a(b)c")
        self.assertIsNone(bib.normal_doi("not a doi"))

    def test_case(self):
        self.assertEqual(bib.title_case("a study of the DNA of mice"), "A Study of the DNA of Mice")
        self.assertEqual(bib.sentence_case("A STUDY OF SLEEP: THE NEXT STEP", all_caps=True), "A study of sleep: The next step")
        self.assertEqual(bib.sentence_case("the Big Title of {NASA}"), "The big title of {NASA}")


class TestDiagnosis(unittest.TestCase):
    def issues(self, e):
        return " | ".join(i.message for i in bib.diagnose([e]))

    def test_clean_entry(self):
        self.assertEqual(bib.diagnose([GOOD]), [])

    def test_problems(self):
        e = {"type": "article", "key": "x", "title": "T.", "year": "c2021", "pages": "1-9", "doi": "https://doi.org/10.1000/a", "author": "John Smith"}
        m = self.issues(e)
        for needle in ("Missing required field: journal", "Odd year", "double hyphen", "link prefix", "Last, First", "full stop"):
            self.assertIn(needle, m)

    def test_misfiled_conference_paper(self):
        e = {"type": "article", "key": "x", "author": "A, B", "title": "T", "booktitle": "Proc", "year": "2020"}
        self.assertIn("inproceedings", self.issues(e))

    def test_duplicate_keys_get_new_ones(self):
        es = [dict(GOOD), dict(GOOD, title="Other", doi="10.1000/other")]
        self.assertTrue(any("Duplicate citation key" in i.message for i in bib.diagnose(es)))
        fixed, _ = bib.apply_fixes(es)
        self.assertEqual(len({e["key"] for e in fixed}), 2)

    def test_repairs_clear_their_issues(self):
        entries, _ = bib.parse_bibtex(bib.SAMPLE)
        fixed, changes = bib.apply_fixes(entries)
        self.assertTrue(changes)
        again, more = bib.apply_fixes(fixed)
        self.assertEqual(again, fixed)
        self.assertEqual(more, [])
        s = fixed[0]
        self.assertEqual((s["author"], s["doi"], s["pages"]), ("Smith, John and de Souza, Ana", "10.1000/xyz123", "10--20"))
        self.assertEqual(s["title"], "A study of sleep and memory")


class TestDuplicates(unittest.TestCase):
    def test_finds_and_merges(self):
        es, _ = bib.parse_bibtex(bib.SAMPLE)
        groups = bib.duplicates(es)
        self.assertEqual(groups, [[0, 1]])
        merged = bib.merge(es, groups[0])
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0]["number"], "3")                 # a field only the duplicate had is kept
        self.assertEqual(merged[0]["volume"], "12")

    def test_doi_match_and_year_guard(self):
        a = dict(GOOD)
        b = dict(GOOD, key="z", title="Completely different", doi="https://doi.org/10.1000/ABC")
        self.assertEqual(bib.duplicates([a, b]), [[0, 1]])
        c = {"type": "article", "key": "c", "title": GOOD["title"], "year": "1999"}
        self.assertEqual(bib.duplicates([a, c]), [])
        out, n = bib.merge_all([a, b, dict(a, key="y")])
        self.assertEqual((len(out), n), (1, 2))


class TestFormatting(unittest.TestCase):
    def test_styles(self):
        self.assertEqual(bib.format_entry(GOOD, "APA"), "Lee, A., & Park, J. (2020). A study of things. J. Things, 3(2), 10-20. https://doi.org/10.1000/abc")
        self.assertEqual(bib.format_entry(GOOD, "IEEE"), 'A. Lee and J. Park, "A study of things," J. Things, vol. 3, no. 2, pp. 10-20, 2020. doi: 10.1000/abc.')
        self.assertIn("Lee, A. and Park, J. (2020) 'A study of things'", bib.format_entry(GOOD, "Harvard"))
        self.assertIn("Lee, A., and J. Park. 2020.", bib.format_entry(GOOD, "Chicago"))
        self.assertIn("Lee A, Park J. A study of things.", bib.format_entry(GOOD, "Vancouver"))
        with self.assertRaises(ValueError):
            bib.format_entry(GOOD, "MLA")

    def test_list(self):
        b = dict(GOOD, author="Adams, Zed")
        self.assertTrue(bib.reference_list([GOOD, b]).startswith("Adams"))
        self.assertTrue(bib.reference_list([GOOD, b], "IEEE").startswith("[1] A. Lee"))

    def test_keys(self):
        self.assertEqual(bib.make_key(GOOD, set()), "lee2020study")
        self.assertEqual(bib.make_key(GOOD, {"lee2020study"}), "lee2020studyb")


class TestGui(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = ak.Store(A.SCHEMA, os.path.join(self.dir, "t.sqlite3"), A.APP.APP_NAME)
        A.sample(self.store)
        try:
            self.app = A.APP(self.store)
        except tk.TclError as exc:
            self.skipTest(str(exc))
        self.errors = []
        self.app.report_callback_exception = lambda e, v, t: self.errors.append(repr(v))
        self.app.update()

    def tearDown(self):
        self.store.close()
        self.app.destroy()
        shutil.rmtree(self.dir, ignore_errors=True)
        self.assertEqual(self.errors, [])

    def test_every_screen_in_both_themes(self):
        for theme in ("Dark", "Light"):
            self.app.set_theme(theme)
            for t in self.app.titles():
                self.app.navigate(t)
                self.app.update()

    def test_repair_flow_persists(self):
        d = self.app.views["Diagnosis"]
        before = len(d.table.tree.get_children())
        d.repair()
        d.dedupe()
        self.assertLess(len(d.table.tree.get_children()), before)
        self.assertEqual(len(json.loads(self.store.get_setting("current"))), 3)

    def test_edit_entry(self):
        self.app.navigate("Entries")
        e = self.app.views["Entries"]
        ak.set_text(e.text, "@article{new, title={Edited}, year={2001}}")
        e.save()
        self.assertEqual(self.app.model.entries[0]["key"], "new")
        ak.set_text(e.text, "garbage")
        with mock.patch("tkinter.messagebox.showerror") as err:
            e.save()
        self.assertTrue(err.called)
        self.assertEqual(self.app.model.entries[0]["key"], "new")

    def test_convert_and_load(self):
        self.app.navigate("Convert and format")
        c = self.app.views["Convert and format"]
        for f in bib.STYLES + ["BibTeX", "RIS", "CSL JSON"]:
            c.fmt.set(f)
            c.show()
            self.assertTrue(ak.get_text(c.text).strip(), f)
        d = self.app.views["Diagnosis"]
        with mock.patch("tkinter.messagebox.showerror") as err:
            self.assertFalse(d.load("nothing useful"))
        self.assertTrue(err.called)
        self.assertTrue(d.load("TY  - JOUR\nTI  - Only one\nER  - \n"))
        self.assertEqual(len(self.app.model.entries), 1)

    def test_save_library(self):
        self.app.navigate("Saved libraries")
        l = self.app.views["Saved libraries"]
        l.keep("Mine")
        self.assertEqual(self.store.list("library")[0]["entries"], 4)


if __name__ == "__main__":
    unittest.main()
