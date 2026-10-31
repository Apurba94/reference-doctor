"""Bibliography engine. Entries are dicts: {'type', 'key', fields...}. Parsing keeps every field, so nothing the user
wrote is lost, and each repair is reported so it can be reviewed."""
import json
import re
from difflib import SequenceMatcher

REQUIRED = {"article": ["author", "title", "journal", "year"], "book": ["title", "publisher", "year"], "inproceedings": ["author", "title", "booktitle", "year"],
            "incollection": ["author", "title", "booktitle", "publisher", "year"], "phdthesis": ["author", "title", "school", "year"],
            "mastersthesis": ["author", "title", "school", "year"], "techreport": ["author", "title", "institution", "year"], "misc": ["title"],
            "unpublished": ["author", "title", "note"]}
RECOMMENDED = {"article": ["volume", "pages", "doi"], "inproceedings": ["pages", "doi"], "book": ["author"]}
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "nor", "of", "on", "or", "over", "the", "to", "up", "via", "with"}
RIS_TYPE = {"article": "JOUR", "book": "BOOK", "incollection": "CHAP", "inproceedings": "CONF", "phdthesis": "THES", "mastersthesis": "THES", "techreport": "RPRT", "misc": "GEN"}
RIS_BACK = {"JOUR": "article", "BOOK": "book", "CHAP": "incollection", "CONF": "inproceedings", "CPAPER": "inproceedings", "THES": "phdthesis", "RPRT": "techreport", "GEN": "misc"}


class Issue:
    def __init__(self, key, level, field, message, fix=None):
        self.key, self.level, self.field, self.message, self.fix = key, level, field, message, fix


def _balanced(text, i):
    open_, close = text[i], "}" if text[i] == "{" else ")"
    depth, j = 0, i
    while j < len(text):
        if text[j] == open_:
            depth += 1
        elif text[j] == close:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def parse_fields(body):
    out = {}
    i, n = 0, len(body)
    while i < n:
        m = re.compile(r"[\s,]*([\w:.+-]+)\s*=\s*").match(body, i)
        if not m:
            break
        name = m.group(1).lower()
        i = m.end()
        if i >= n:
            break
        if body[i] == "{":
            j = _balanced(body, i)
            if j < 0:
                raise ValueError("unbalanced braces in field %s" % name)
            value = body[i + 1:j]
            i = j + 1
        elif body[i] == '"':
            j = i + 1
            depth = 0
            while j < n and (body[j] != '"' or depth):
                depth += {"{": 1, "}": -1}.get(body[j], 0)
                j += 1
            value = body[i + 1:j]
            i = j + 1
        else:
            j = i
            while j < n and body[j] not in ",\n":
                j += 1
            value = body[i:j].strip()
            i = j
        out[name] = re.sub(r"\s+", " ", value).strip()
    return out


def parse_bibtex(text):
    """Returns (entries, strings of problems found while reading)."""
    entries, problems = [], []
    pos = 0
    pattern = re.compile(r"@(\w+)\s*([{(])")
    while True:
        m = pattern.search(text, pos)
        if not m:
            break
        kind = m.group(1).lower()
        end = _balanced(text, m.end() - 1)
        if end < 0:
            problems.append("Unbalanced braces in the entry starting at character %d." % m.start())
            pos = m.end()
            continue
        body = text[m.end():end]
        pos = end + 1
        if kind in ("comment", "preamble", "string"):
            continue
        key, _, rest = body.partition(",")
        try:
            entry = {"type": kind, "key": key.strip()}
            entry.update(parse_fields(rest))
            entries.append(entry)
        except ValueError as exc:
            problems.append("%s: %s" % (key.strip(), exc))
    return entries, problems


def parse_ris(text):
    entries, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9])\s{2}-\s?(.*)$", line.rstrip())
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"type": RIS_BACK.get(val, "misc"), "key": "", "_authors": []}
        elif cur is None:
            continue
        elif tag == "ER":
            if cur["_authors"]:
                cur["author"] = " and ".join(cur["_authors"])
            del cur["_authors"]
            entries.append(cur)
            cur = None
        elif tag in ("AU", "A1"):
            cur["_authors"].append(val)
        elif tag in ("TI", "T1"):
            cur["title"] = val
        elif tag in ("JO", "JF", "T2", "BT"):
            cur["booktitle" if cur["type"] in ("inproceedings", "incollection") else "journal"] = val
        elif tag in ("PY", "Y1", "DA"):
            y = re.search(r"\d{4}", val)
            if y:
                cur["year"] = y.group()
        elif tag == "VL":
            cur["volume"] = val
        elif tag == "IS":
            cur["number"] = val
        elif tag == "SP":
            cur["pages"] = val
        elif tag == "EP":
            cur["pages"] = "%s--%s" % (cur.get("pages", ""), val)
        elif tag == "DO":
            cur["doi"] = val
        elif tag == "UR":
            cur["url"] = val
        elif tag == "PB":
            cur["publisher"] = val
    return entries


def parse_csl(text):
    data = json.loads(text)
    out = []
    types = {"article-journal": "article", "book": "book", "chapter": "incollection", "paper-conference": "inproceedings", "thesis": "phdthesis", "report": "techreport"}
    for r in data:
        e = {"type": types.get(r.get("type"), "misc"), "key": r.get("id", "")}
        names = []
        for a in r.get("author", []):
            names.append("%s, %s" % (a.get("family", ""), a.get("given", "")) if a.get("given") else a.get("family", a.get("literal", "")))
        if names:
            e["author"] = " and ".join(names)
        for k, f in (("title", "title"), ("container-title", "journal"), ("volume", "volume"), ("issue", "number"), ("page", "pages"), ("DOI", "doi"), ("URL", "url"), ("publisher", "publisher")):
            if r.get(k):
                e[f] = str(r[k])
        parts = (r.get("issued") or {}).get("date-parts")
        if parts and parts[0]:
            e["year"] = str(parts[0][0])
        out.append(e)
    return out


def parse_any(text):
    """(entries, problems, format)."""
    s = text.strip()
    if s.startswith("[") or s.startswith("{"):
        return parse_csl(s), [], "CSL JSON"
    if re.search(r"^TY\s{2}-", s, re.M):
        return parse_ris(s), [], "RIS"
    if re.search(r"@\w+\s*[{(]", s):
        e, p = parse_bibtex(s)
        return e, p, "BibTeX"
    raise ValueError("this does not look like BibTeX, RIS or CSL JSON")


def to_bibtex(entries):
    out = []
    order = ["author", "title", "journal", "booktitle", "year", "volume", "number", "pages", "publisher", "school", "institution", "doi", "url"]
    for e in entries:
        keys = [k for k in order if k in e] + sorted(k for k in e if k not in order and k not in ("type", "key"))
        out.append("@%s{%s,\n%s\n}" % (e["type"], e["key"], ",\n".join("  %s = {%s}" % (k, e[k]) for k in keys)))
    return "\n\n".join(out) + "\n"


def to_ris(entries):
    out = []
    for e in entries:
        rows = ["TY  - %s" % RIS_TYPE.get(e["type"], "GEN")]
        rows += ["AU  - %s" % a for a in split_authors(e.get("author", ""))]
        for tag, f in (("TI", "title"), ("JO", "journal"), ("T2", "booktitle"), ("PY", "year"), ("VL", "volume"), ("IS", "number"), ("DO", "doi"), ("UR", "url"), ("PB", "publisher")):
            if e.get(f):
                rows.append("%s  - %s" % (tag, e[f]))
        if e.get("pages"):
            a, _, b = e["pages"].replace("--", "-").partition("-")
            rows.append("SP  - %s" % a)
            if b:
                rows.append("EP  - %s" % b)
        out.append("\n".join(rows + ["ER  - "]))
    return "\n\n".join(out) + "\n"


def to_csl(entries):
    types = {"article": "article-journal", "book": "book", "incollection": "chapter", "inproceedings": "paper-conference", "phdthesis": "thesis", "techreport": "report"}
    rows = []
    for e in entries:
        r = {"id": e["key"], "type": types.get(e["type"], "document")}
        names = []
        for a in split_authors(e.get("author", "")):
            family, _, given = a.partition(",")
            names.append({"family": family.strip(), "given": given.strip()})
        if names:
            r["author"] = names
        for k, f in (("title", "title"), ("container-title", "journal"), ("volume", "volume"), ("issue", "number"), ("page", "pages"), ("DOI", "doi"), ("URL", "url"), ("publisher", "publisher")):
            if e.get(f):
                r[k] = e[f].replace("--", "-") if f == "pages" else e[f]
        if e.get("year", "").isdigit():
            r["issued"] = {"date-parts": [[int(e["year"])]]}
        rows.append(r)
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"


def split_authors(text):
    return [a.strip() for a in re.split(r"\s+and\s+", text or "") if a.strip()]


def normal_author(a):
    """'First Last' or 'Last, First' -> 'Last, First'."""
    a = a.strip()
    if "," in a or a.lower() == "others" or a.startswith("{"):
        return a
    parts = a.split()
    if len(parts) == 1:
        return a
    i = len(parts) - 1
    while i > 1 and parts[i - 1][0].islower():            # keep particles: van der Berg
        i -= 1
    return "%s, %s" % (" ".join(parts[i:]), " ".join(parts[:i]))


def normal_doi(v):
    m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", v or "")
    return m.group().rstrip(".,;)") if m else None


def title_case(t):
    """Headline capitalisation that leaves acronyms and braced words alone."""
    words = t.split(" ")
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if w.startswith("{") or (len(w) > 1 and w.isupper()) or re.search(r"[a-z][A-Z]", w):
            out.append(w)
        elif i and i < len(words) - 1 and low in SMALL and not out[-1].endswith(":"):
            out.append(low)
        else:
            out.append(w[:1].upper() + w[1:].lower() if w.isupper() or w.islower() else w)
    return " ".join(out)


def sentence_case(t, all_caps=False):
    """all_caps: the whole title is in capitals, so a capitalised word says nothing about acronyms."""
    words = t.split(" ")
    out = []
    for i, w in enumerate(words):
        if w.startswith("{") or (not all_caps and ((len(w) > 1 and w.isupper()) or re.search(r"[a-z][A-Z]", w))):
            out.append(w)
        elif i == 0 or out[-1].endswith(":"):
            out.append(w[:1].upper() + w[1:].lower())
        else:
            out.append(w.lower())
    return " ".join(out)


def make_key(e, taken):
    first = split_authors(e.get("author", "") or e.get("editor", ""))
    fam = re.sub(r"\W", "", normal_author(first[0]).split(",")[0]).lower() if first else "anon"
    word = next((w for w in re.findall(r"[A-Za-z]+", e.get("title", "")) if len(w) > 3), "").lower()
    base = key = "%s%s%s" % (fam, e.get("year", "nd"), word)
    n = 1
    while key in taken:
        n += 1
        key = "%s%s" % (base, chr(96 + n))
    return key


def diagnose(entries):
    """All problems, each with a repair where one is safe."""
    out = []
    seen = {}
    for e in entries:
        k = e["key"] or "(no key)"
        need = REQUIRED.get(e["type"])
        if need is None:
            out.append(Issue(k, "warning", "type", "Unusual entry type @%s." % e["type"]))
            need = REQUIRED["misc"]
        for f in need:
            if not e.get(f):
                if f == "journal" and e.get("booktitle"):
                    out.append(Issue(k, "warning", "type", "An article with a booktitle: this is probably @inproceedings.", ("type", "inproceedings")))
                else:
                    out.append(Issue(k, "error", f, "Missing required field: %s." % f))
        for f in RECOMMENDED.get(e["type"], []):
            if not e.get(f):
                out.append(Issue(k, "info", f, "Missing recommended field: %s." % f))
        if not e["key"]:
            out.append(Issue(k, "error", "key", "No citation key.", ("key", None)))
        elif e["key"] in seen:
            out.append(Issue(k, "error", "key", "Duplicate citation key.", ("key", None)))
        seen[e["key"]] = True
        y = e.get("year", "")
        if y and not re.fullmatch(r"(1[5-9]|20)\d{2}[a-z]?", y):
            m = re.search(r"(1[5-9]|20)\d{2}", y)
            out.append(Issue(k, "warning", "year", "Odd year: %s." % y, ("year", m.group()) if m else None))
        p = e.get("pages", "")
        if p and re.search(r"\d\s*[-–—]\s*\d", p) and "--" not in p:
            out.append(Issue(k, "info", "pages", "Page range should use a double hyphen.", ("pages", re.sub(r"\s*[-–—]+\s*", "--", p))))
        d = e.get("doi", "")
        if d:
            nd = normal_doi(d)
            if nd is None:
                out.append(Issue(k, "warning", "doi", "This DOI looks wrong: %s." % d))
            elif nd != d:
                out.append(Issue(k, "info", "doi", "DOI should not include a link prefix.", ("doi", nd)))
        a = e.get("author", "")
        if a:
            fixed = " and ".join(normal_author(x) for x in split_authors(a))
            if fixed != a:
                out.append(Issue(k, "info", "author", "Author names are not in Last, First form.", ("author", fixed)))
            if ";" in a or (a.count(",") > len(split_authors(a)) and " and " not in a):
                out.append(Issue(k, "warning", "author", "Separate authors with 'and', not commas or semicolons."))
        t = e.get("title", "")
        if t and t.isupper() and len(t) > 8:
            out.append(Issue(k, "warning", "title", "The title is in capitals.", ("title", sentence_case(t, all_caps=True))))
        if t.endswith("."):
            out.append(Issue(k, "info", "title", "The title ends with a full stop.", ("title", t.rstrip("."))))
        if e.get("url", "").startswith("https://doi.org") and d:
            out.append(Issue(k, "info", "url", "The URL only repeats the DOI.", ("url", "")))
    return out


def apply_fixes(entries, issues=None):
    """Apply every safe repair. Returns (new entries, list of change descriptions)."""
    entries = [dict(e) for e in entries]
    by_key = {}
    for e in entries:
        by_key.setdefault(e["key"] or "(no key)", []).append(e)
    changes = []
    taken = {e["key"] for e in entries if e["key"]}
    for i in (issues if issues is not None else diagnose(entries)):
        if not i.fix:
            continue
        field, value = i.fix
        targets = by_key.get(i.key, [])
        if field == "key":
            for e in targets[1:] if e_has_key(targets) else targets:
                new = make_key(e, taken)
                taken.add(new)
                changes.append("%s: key becomes %s" % (i.key, new))
                e["key"] = new
            continue
        for e in targets:
            if value == "":
                e.pop(field, None)
            else:
                e[field] = value
            changes.append("%s: %s" % (i.key, i.message))
    return entries, changes


def e_has_key(targets):
    return bool(targets and targets[0]["key"])


def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def duplicates(entries, threshold=0.9):
    """Groups of probable duplicates, by DOI or a near-identical title with the same year."""
    groups, used = [], set()
    for i, a in enumerate(entries):
        if i in used:
            continue
        group = [i]
        for j in range(i + 1, len(entries)):
            if j in used:
                continue
            b = entries[j]
            da, db = normal_doi(a.get("doi")), normal_doi(b.get("doi"))
            same = bool(da and db and da.lower() == db.lower())
            if not same and a.get("year", "") == b.get("year", ""):
                same = SequenceMatcher(None, _norm(a.get("title")), _norm(b.get("title"))).ratio() >= threshold
            if same:
                group.append(j)
                used.add(j)
        if len(group) > 1:
            groups.append(group)
            used.add(i)
    return groups


def merge(entries, group):
    """Combine a duplicate group into its first entry, keeping any field the first lacks."""
    keep = dict(entries[group[0]])
    for i in group[1:]:
        for k, v in entries[i].items():
            if k not in keep or not keep[k]:
                keep[k] = v
    out = [e for i, e in enumerate(entries) if i not in group[1:]]
    out[out.index(entries[group[0]])] = keep
    return out


def merge_all(entries):
    count = 0
    while True:
        g = duplicates(entries)
        if not g:
            return entries, count
        count += len(g[0]) - 1
        entries = merge(entries, g[0])


def _names(e, style):
    parts = split_authors(e.get("author", ""))
    names = []
    for a in parts:
        fam, _, given = normal_author(a).partition(",")
        ini = [g[0].upper() + "." for g in re.split(r"[\s.-]+", given) if g]
        names.append((fam.strip(), ini))
    if not names:
        return "Anonymous"
    if style == "APA":
        fmt = ["%s, %s" % (f, " ".join(i)) if i else f for f, i in names]
        return fmt[0] if len(fmt) == 1 else ", ".join(fmt[:-1]) + ", & " + fmt[-1]
    if style == "IEEE":
        fmt = ["%s %s" % (" ".join(i), f) if i else f for f, i in names]
        return fmt[0] if len(fmt) == 1 else ", ".join(fmt[:-1]) + " and " + fmt[-1]
    if style == "Harvard":
        fmt = ["%s, %s" % (f, "".join(i)) if i else f for f, i in names]
        return fmt[0] if len(fmt) == 1 else ", ".join(fmt[:-1]) + " and " + fmt[-1]
    if style == "Chicago":
        first = names[0]
        rest = ["%s %s" % (" ".join(i), f) for f, i in names[1:]]
        lead = "%s, %s" % (first[0], " ".join(first[1]))
        return lead if not rest else lead + ", and " + ", ".join(rest)
    return ", ".join("%s %s" % (f, "".join(x[0] for x in i)) for f, i in names[:6]) + (", et al" if len(names) > 6 else "")


def format_entry(e, style="APA"):
    title = (e.get("title") or "").replace("{", "").replace("}", "").rstrip(".")
    venue = e.get("journal") or e.get("booktitle") or e.get("publisher") or e.get("school") or ""
    year = e.get("year", "n.d.")
    vol, num, pages = e.get("volume"), e.get("number"), (e.get("pages") or "").replace("--", "-")
    doi = normal_doi(e.get("doi"))
    who = _names(e, style)
    if style == "APA":
        s = "%s (%s). %s." % (who, year, title)
        if venue:
            s += " %s" % venue
            if vol:
                s += ", %s%s" % (vol, "(%s)" % num if num else "")
            if pages:
                s += ", %s" % pages
            s += "."
        return s + (" https://doi.org/%s" % doi if doi else "")
    if style == "IEEE":
        s = '%s, "%s,"' % (who, title)
        for part in (venue, "vol. %s" % vol if vol else "", "no. %s" % num if num else "", "pp. %s" % pages if pages else "", year):
            if part:
                s += " %s," % part
        return s.rstrip(",") + "." + (" doi: %s." % doi if doi else "")
    if style == "Harvard":
        s = "%s (%s) '%s'" % (who, year, title)
        if venue:
            s += ", %s" % venue
        if vol:
            s += ", %s%s" % (vol, "(%s)" % num if num else "")
        if pages:
            s += ", pp. %s" % pages
        return s + "." + (" doi:%s." % doi if doi else "")
    if style == "Chicago":
        s = '%s. %s. "%s."' % (who, year, title)
        if venue:
            s += " %s" % venue
        if vol:
            s += " %s%s" % (vol, " (%s)" % num if num else "")
        if pages:
            s += ": %s" % pages
        return s + "." + (" https://doi.org/%s." % doi if doi else "")
    if style == "Vancouver":
        s = "%s. %s." % (who, title)
        if venue:
            s += " %s. %s" % (venue, year)
            if vol:
                s += ";%s%s" % (vol, "(%s)" % num if num else "")
            if pages:
                s += ":%s" % pages
            s += "."
        return s
    raise ValueError("unknown style %r" % style)


STYLES = ["APA", "IEEE", "Harvard", "Chicago", "Vancouver"]


def reference_list(entries, style="APA"):
    if style in ("IEEE", "Vancouver"):
        return "\n".join("[%d] %s" % (i, format_entry(e, style)) for i, e in enumerate(entries, 1))
    return "\n".join(sorted((format_entry(e, style) for e in entries), key=str.lower))


def summary(entries):
    years = sorted(int(e["year"]) for e in entries if e.get("year", "").isdigit())
    types = {}
    for e in entries:
        types[e["type"]] = types.get(e["type"], 0) + 1
    return {"entries": len(entries), "types": types, "oldest": years[0] if years else None, "newest": years[-1] if years else None,
            "with_doi": sum(1 for e in entries if e.get("doi"))}


SAMPLE = """@article{smith2019,
  author = {John Smith and Ana de Souza},
  title = {A STUDY OF SLEEP AND MEMORY},
  journal = {Journal of Sleep},
  year = {2019},
  volume = {12},
  pages = {10-20},
  doi = {https://doi.org/10.1000/xyz123}
}

@article{smith2019b,
  author = {Smith, John and de Souza, Ana},
  title = {A study of sleep and memory.},
  journal = {J. Sleep},
  year = {2019},
  number = {3}
}

@article{lee,
  author = {Lee, Min},
  title = {Conference talk stored as an article},
  booktitle = {Proceedings of Testing},
  year = {c2021}
}

@book{lee,
  title = {A book without a publisher},
  year = {2015}
}
"""
