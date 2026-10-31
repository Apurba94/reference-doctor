# Reference Doctor

Bibliography files collect errors over the years: capitalised titles, DOIs pasted as links, a conference paper filed as a journal
article, the same paper twice under different keys. This program diagnoses them and repairs what is safe.

* **Reads** BibTeX, RIS and CSL JSON. A broken entry is reported and the rest of the file still loads.
* **Diagnosis:** missing required fields by entry type, duplicate keys, odd years, page ranges, DOI format, author name form, titles
  in capitals, trailing full stops and misfiled entry types.
* **Safe repairs:** one click applies each fix that needs no judgement, and everything else stays listed for you. Nothing is
  dropped: fields the program does not know are kept.
* **Duplicates:** by DOI or near-identical title in the same year. Merging keeps any field only the duplicate had.
* **Convert** between BibTeX, RIS and CSL JSON.
* **Format** reference lists in APA, IEEE, Harvard, Chicago or Vancouver style.
* **Edit** any entry as BibTeX, and save libraries by name.

`--demo` opens a temporary database with a deliberately messy sample file.

## From source

```bash
python -m reference_doctor
python -m unittest discover -s tests -t .
python build_exe.py
```

## Limits

The program works offline, so it cannot look up missing details: it tells you what is missing and you fill it in. Formatted
references follow the common pattern of each style, so check them against the exact guide your publisher uses.

## License

MIT
