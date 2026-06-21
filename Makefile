TEXFILE  = test_paper
TYPFILE  = test_paper
BIBFILE  = refs
CSL      = styles/physics-in-medicine-and-biology.csl
PYTHON   = bash run.sh
PANDOC   = pandoc

.PHONY: all pdf pdf-latexmk pdf-manual word word-typ typ-pdf clean cleanall setup

all: pdf word

# ── 环境安装 ──────────────────────────────────────────────────────────

setup:
	uv venv .venv
	uv pip install -r pyproject.toml

# ── PDF ──────────────────────────────────────────────────────────────

pdf: pdf-latexmk

pdf-latexmk:
	latexmk -xelatex -interaction=nonstopmode $(TEXFILE).tex

pdf-manual:
	xelatex -interaction=nonstopmode $(TEXFILE).tex
	bibtex $(TEXFILE)
	xelatex -interaction=nonstopmode $(TEXFILE).tex
	xelatex -interaction=nonstopmode $(TEXFILE).tex

# ── Word ─────────────────────────────────────────────────────────────

word: pandoc_output.docx mapping.json
	$(PYTHON) scripts/inject_zotero.py \
		--input pandoc_output.docx \
		--output $(TEXFILE)_zotero.docx \
		--mapping mapping.json \
		--csl $(CSL) \
		--bib $(BIBFILE).bib \
		--user-id 0

pandoc_output.docx: $(TEXFILE).tex $(BIBFILE).bib
	$(PANDOC) $(TEXFILE).tex --citeproc -M link-citations=true \
		--bibliography=$(BIBFILE).bib --csl=$(CSL) -o pandoc_output.docx

# ── Typst PDF ─────────────────────────────────────────────────────────

typ-pdf: $(TYPFILE).pdf

$(TYPFILE).pdf: $(TYPFILE).typ $(BIBFILE).bib
	typst compile $(TYPFILE).typ

# ── Typst Word ─────────────────────────────────────────────────────────

word-typ: pandoc_output_typ.docx mapping.json
	$(PYTHON) scripts/inject_zotero.py \
		--input pandoc_output_typ.docx \
		--output $(TYPFILE)_typ_zotero.docx \
		--mapping mapping.json \
		--csl $(CSL) \
		--bib $(BIBFILE).bib \
		--user-id 0

pandoc_output_typ.docx: $(TYPFILE).typ $(BIBFILE).bib
	$(PANDOC) $(TYPFILE).typ --citeproc -M link-citations=true \
		--bibliography=$(BIBFILE).bib --csl=$(CSL) -o pandoc_output_typ.docx

mapping.json: $(BIBFILE).bib
	$(PYTHON) -c "\
from pyzotero import zotero; import bibtexparser, json; \
zot = zotero.Zotero(0, 'user', local=True); \
db = bibtexparser.load(open('$(BIBFILE).bib', encoding='utf-8')); \
bib = {e['ID']: (e.get('doi','').strip().lower() or None) for e in db.entries}; \
items = zot.everything(zot.items(itemType='journalArticle')); \
doi2key = {(it['data'].get('DOI') or '').strip().lower(): it['data']['key'] for it in items if (it['data'].get('DOI') or '').strip()}; \
mapping = {ck: {'zotero_key': doi2key[doi], 'anchor': 'doi', 'confidence': 'high', 'status': 'PASS'} for ck, doi in bib.items() if doi and doi in doi2key}; \
json.dump(mapping, open('mapping.json','w'), ensure_ascii=False, indent=2); \
print(f'mapping: {len(mapping)}/{len(bib)}')"

# ── Clean ─────────────────────────────────────────────────────────────

clean:
	latexmk -c -bibtex $(TEXFILE).tex
	rm -f $(TEXFILE).dvi pandoc_output.docx pandoc_output_typ.docx pandoc_output_md.docx mapping.json

cleanall: clean
	rm -f $(TEXFILE).pdf $(TYPFILE).pdf \
		$(TEXFILE)_zotero.docx $(TYPFILE)_typ_zotero.docx test_paper_md_zotero.docx
