#!/usr/bin/env python3
"""
inject_anyword.py — Inject Zotero CSL_CITATION field codes into any Word document.

Supports two mapping modes:
  1. --mapping: pre-built JSON mapping (number → Zotero key)
  2. auto-build: parse bibliography from docx to extract [N] → DOI/title → Zotero key

Usage:
    # With pre-built mapping
    python3 inject_anyword.py --input paper.docx --output paper_zotero.docx \
        --mapping mapping.json --user-id 0

    # Auto-build mapping from docx bibliography
    python3 inject_anyword.py --input paper.docx --output paper_zotero.docx \
        --user-id 0 --build-mapping
"""

import argparse
import json
import os
import random
import re
import string
import sys
import zipfile
from pathlib import Path

from lxml import etree
from docx import Document
from docx.oxml.ns import qn
from pyzotero import zotero


# ── Helpers ───────────────────────────────────────────────────────────

def random_citation_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def build_csl_citation(item_key, user_id):
    uri = f"http://zotero.org/users/local/{user_id}/items/{item_key}"
    return json.dumps({
        "citationID": random_citation_id(),
        "properties": {"noteIndex": 0},
        "citationItems": [{"uris": [uri]}],
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
    }, ensure_ascii=False)


def build_csl_citation_multi(items, user_id):
    citation_items = [{"uris": [f"http://zotero.org/users/local/{user_id}/items/{k}"]} for k in items]
    return json.dumps({
        "citationID": random_citation_id(),
        "properties": {"noteIndex": 0},
        "citationItems": citation_items,
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
    }, ensure_ascii=False)


def make_run(xml_str):
    return etree.fromstring(xml_str)


def escape_xml(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def create_zotero_field(display_text, csl_json, rPr_xml=None, superscript=False):
    instr_text = f" ADDIN ZOTERO_ITEM CSL_CITATION {csl_json} "
    runs = []

    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:fldChar w:fldCharType="begin"/></w:r>'))

    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:instrText xml:space="preserve">{}</w:instrText>'
        '</w:r>'.format(escape_xml(instr_text))))

    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:fldChar w:fldCharType="separate"/></w:r>'))

    if rPr_xml:
        display_rpr = rPr_xml.replace('</w:rPr>', '<w:rStyle w:val="ZoteroCitation"/></w:rPr>')
        display_xml = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'{display_rpr}<w:t xml:space="preserve">{escape_xml(display_text)}</w:t></w:r>')
    elif superscript:
        display_xml = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:rPr><w:rStyle w:val="ZoteroCitation"/>'
            '<w:vertAlign w:val="superscript"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape_xml(display_text)}</w:t></w:r>')
    else:
        display_xml = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape_xml(display_text)}</w:t></w:r>')
    runs.append(make_run(display_xml))

    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:fldChar w:fldCharType="end"/></w:r>'))

    return runs


# ── Numeric citation detection ────────────────────────────────────────

NUMERIC_RE = re.compile(r'^(\d+(?:\s*,\s*\d+)*)$')
BRACKET_RE = re.compile(r'^\[(\d+(?:\s*[;,]\s*\d+)*)\]$')
SINGLE_BRACKET_RE = re.compile(r'\[(\d+)\]')


def is_superscript_citation_run(r_elem):
    rPr = r_elem.find(qn('w:rPr'))
    if rPr is None:
        return False
    va = rPr.find(qn('w:vertAlign'))
    if va is None or va.get(qn('w:val')) != 'superscript':
        return False
    t_elem = r_elem.find(qn('w:t'))
    if t_elem is None or t_elem.text is None:
        return False
    return bool(NUMERIC_RE.match(t_elem.text.strip()))


def is_bracket_citation_run(r_elem):
    """Check if a <w:r> is a bracket-style citation like [1], [2, 3], or [2], [3]."""
    t_elem = r_elem.find(qn('w:t'))
    if t_elem is None or t_elem.text is None:
        return False
    text = t_elem.text.strip()
    if BRACKET_RE.match(text):
        return True
    parts = re.split(r'\s*[,;]\s*', text)
    return len(parts) > 1 and all(SINGLE_BRACKET_RE.match(p.strip()) for p in parts if p.strip())


def parse_numeric_text(text):
    """Parse citation text into list of ints. Handles '1', '2,3', '[2, 3]', '[1]', '[2], [3]'."""
    cleaned = text.strip()
    if ',]' in cleaned or '];' in cleaned or (cleaned.count('[') > 1 and cleaned.count(']') > 1):
        nums = [int(m) for m in SINGLE_BRACKET_RE.findall(cleaned)]
        return nums if nums else [0]
    cleaned = cleaned.strip('[]')
    parts = re.split(r'\s*[,;]\s*', cleaned)
    return [int(p.strip()) for p in parts if p.strip().isdigit()]


# ── Author-year text matching ─────────────────────────────────────────

def load_bib_lookup(bib_path):
    import bibtexparser
    with open(bib_path, encoding='utf-8') as f:
        db = bibtexparser.load(f)
    lookup = {}
    for entry in db.entries:
        authors = []
        for a in entry.get('author', '').split(' and '):
            parts = a.strip().split(',')
            if parts[0].strip():
                authors.append(parts[0].strip())
        lookup[entry['ID']] = {
            'authors': authors,
            'year': entry.get('year', ''),
        }
    return lookup


def match_author_year_text(text, bib_lookup):
    matches = []
    for cite_key, info in bib_lookup.items():
        year = info['year']
        authors = info['authors']
        if year and year not in text:
            continue
        for author in authors:
            if len(author.split()) > 1:
                last_word = author.split()[-1]
                if last_word in text:
                    matches.append(cite_key)
                    break
            else:
                if author in text:
                    matches.append(cite_key)
                    break
    return matches


def inject_author_year_text(body, citation_map, user_id, bib_path):
    if not bib_path:
        print("  ⚠ 文本匹配需要 --bib")
        return 0, ["missing --bib"]
    bib_lookup = load_bib_lookup(bib_path)
    total, warnings = 0, []
    for p_elem in body.iter(qn('w:p')):
        runs = list(p_elem.findall(qn('w:r')))
        consumed = set()
        for idx, r in enumerate(runs):
            if id(r) in consumed:
                continue
            t = r.find(qn('w:t'))
            txt = t.text if (t is not None and t.text) else ''
            if '(' not in txt:
                continue
            group, gtext = [r], txt
            k = idx + 1
            while ')' not in gtext and k < len(runs):
                if id(runs[k]) in consumed:
                    break
                group.append(runs[k])
                nt = runs[k].find(qn('w:t'))
                if nt is not None and nt.text:
                    gtext += nt.text
                k += 1
            if ')' not in gtext:
                continue
            cites = re.findall(r'\(([^)]+)\)', gtext)
            if not cites:
                continue
            cite_keys = []
            for c in cites:
                cite_keys.extend(match_author_year_text(c, bib_lookup))
            item_keys = [citation_map.get(ck) for ck in cite_keys if citation_map.get(ck)]
            for ck in cite_keys:
                if not citation_map.get(ck):
                    warnings.append(f"No Zotero key for cite_key: {ck}")
            if not item_keys:
                continue
            csl_json = (build_csl_citation(item_keys[0], user_id)
                        if len(item_keys) == 1
                        else build_csl_citation_multi(item_keys, user_id))
            display = '(' + '; '.join(cites) + ')'
            field_runs = create_zotero_field(display, csl_json, superscript=False)
            parent = r.getparent()
            pos = list(parent).index(group[0])
            for fr in field_runs:
                parent.insert(pos, fr)
                pos += 1
            for gr in group:
                consumed.add(id(gr))
                if gr.getparent() is not None:
                    parent.remove(gr)
            total += 1
            print(f"  ✓ Replaced: {display} ({len(item_keys)} items)")
    return total, warnings


# ── Injection ─────────────────────────────────────────────────────────

def inject_numeric(body, citation_map, user_id):
    """Replace numbered citations (superscript or bracket) with Zotero fields."""
    total, warnings = 0, []
    for p_elem in body.iter(qn('w:p')):
        runs_to_replace = []
        for r_elem in list(p_elem):
            if is_superscript_citation_run(r_elem) or is_bracket_citation_run(r_elem):
                runs_to_replace.append(r_elem)
        for r_elem in runs_to_replace:
            t_elem = r_elem.find(qn('w:t'))
            text = t_elem.text.strip()
            nums = parse_numeric_text(text)
            is_bracket = bool(BRACKET_RE.match(text.strip()))
            rPr_xml = (etree.tostring(r_elem.find(qn('w:rPr')), encoding='unicode')
                       if r_elem.find(qn('w:rPr')) is not None else None)
            item_keys = []
            for n in nums:
                key = citation_map.get(str(n))
                if key is None:
                    warnings.append(f"No Zotero key for citation #{n}")
                    break
                item_keys.append(key)
            else:
                csl_json = (build_csl_citation(item_keys[0], user_id)
                            if len(item_keys) == 1
                            else build_csl_citation_multi(item_keys, user_id))
                display = ",".join(str(n) for n in nums)
                if is_bracket:
                    display = f"[{display}]"
                is_super = is_superscript_citation_run(r_elem)
                field_runs = create_zotero_field(display, csl_json, rPr_xml, superscript=is_super)
                parent = r_elem.getparent()
                idx = list(parent).index(r_elem)
                parent.remove(r_elem)
                for i, fr in enumerate(field_runs):
                    parent.insert(idx + i, fr)
                total += 1
                print(f"  ✓ Replaced numeric citation [{display}]")
    return total, warnings


# ── Bibliography handling ─────────────────────────────────────────────

BIB_HEADINGS = {'References', '参考文献', '参考书目', 'Bibliography'}


def remove_references_entries(body):
    """Find bib heading (中/英), keep heading, remove entries after it."""
    refs_heading = None
    for p_elem in body.iter(qn('w:p')):
        text = ''.join(t.text for t in p_elem.iter(qn('w:t')) if t.text).strip()
        if text in BIB_HEADINGS:
            refs_heading = p_elem
            break
    if refs_heading is None:
        print("  ⚠ No bibliography heading found")
        return 0
    elems_to_remove = []
    found = False
    for child in list(body):
        if child is refs_heading:
            found = True
            continue
        if found:
            elems_to_remove.append(child)
    for elem in elems_to_remove:
        body.remove(elem)
    heading_text = ''.join(t.text for t in refs_heading.iter(qn('w:t')) if t.text).strip()
    print(f"  Removed {len(elems_to_remove)} entries after '{heading_text}'")
    return len(elems_to_remove)


def add_zotero_bibliography(body):
    """Add ZOTERO_BIBLIOGRAPH field at end of body."""
    bib_para = etree.SubElement(body, qn('w:p'))
    bib_para.set(qn('w:rsidR'), '00000000')
    bib_para.set(qn('w:rsidRDefault'), '00000000')
    pPr = etree.SubElement(bib_para, qn('w:pPr'))
    pStyle = etree.SubElement(pPr, qn('w:pStyle'))
    pStyle.set(qn('w:val'), 'Bibliography')
    bib_json = json.dumps({
        "bibliographyStyle": "http://www.zotero.org/styles/apa",
        "bibliographyDefaults": "",
        "citationCluster": []
    }, ensure_ascii=False)
    bib_instr = f" ADDIN ZOTERO_BIBLIOGRAPH {bib_json} "
    for r_xml in [
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:fldChar w:fldCharType="begin"/></w:r>',
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:instrText xml:space="preserve">{}</w:instrText></w:r>'.format(escape_xml(bib_instr)),
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:fldChar w:fldCharType="separate"/></w:r>',
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:t xml:space="preserve">[BIBLIOGRAPHY]</w:t></w:r>',
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:fldChar w:fldCharType="end"/></w:r>',
    ]:
        bib_para.append(make_run(r_xml))
    print("  Added ZOTERO_BIBLIOGRAPH field")


def ensure_zotero_style(doc):
    styles_part = doc.styles.element
    for style in styles_part.iter(qn('w:style')):
        if style.get(qn('w:styleId')) == 'ZoteroCitation':
            return
    styles_part.append(make_run(
        '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' w:type="character" w:styleId="ZoteroCitation">'
        '<w:name w:val="ZoteroCitation"/>'
        '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'))
    print("  Added ZoteroCitation character style")


# ── Build mapping from docx ───────────────────────────────────────────

def build_numeric_mapping(input_path, user_id):
    """Parse docx bibliography to build [N] → Zotero key mapping.
    Priority: DOI → exact; no DOI → title similarity.
    """
    z = zipfile.ZipFile(input_path)
    c = z.open('word/document.xml').read().decode()
    texts = re.findall(r'<w:t[^>]*>([^<]+)</w:t>', c)
    full = ''.join(texts)

    # Extract [N] → DOI
    num_doi = {}
    for m in re.finditer(r'\[(\d+)\].*?doi:\s*(10\.\S+?)\.\s*(?=\[|$)', full, re.IGNORECASE | re.DOTALL):
        num_doi[m.group(1)] = m.group(2).rstrip('.').lower()

    # Extract [N] → title (fallback)
    num_title = {}
    for m in re.finditer(r'\[(\d+)\].*?[「「]([^」」]+)[」」]', full):
        num_title[m.group(1)] = m.group(2)

    zot = zotero.Zotero(0, 'user', local=True)
    items = zot.everything(zot.items(itemType='journalArticle'))

    doi2key = {}
    title2key = {}
    for it in items:
        d = (it['data'].get('DOI') or '').strip().lower()
        if d:
            doi2key[d] = it['data']['key']
        t = (it['data'].get('title') or '').strip().lower().rstrip('.')
        if t:
            title2key[t] = it['data']['key']

    def similar(a, b):
        wa, wb = set(a.lower().split()), set(b.lower().split())
        return len(wa & wb) / len(wa | wb) if (wa and wb) else 0

    mapping = {}
    all_nums = sorted(set(list(num_doi.keys()) + list(num_title.keys())),
                      key=lambda x: int(x))
    for num in all_nums:
        if num in num_doi and num_doi[num] in doi2key:
            mapping[num] = doi2key[num_doi[num]]
            print(f"  {num} → DOI match ✅")
        elif num in num_title:
            best, best_score = '', 0
            for zt, zk in title2key.items():
                s = similar(num_title[num], zt)
                if s > best_score:
                    best_score, best = s, zk
            if best_score >= 0.5:
                mapping[num] = best
                print(f"  {num} → title match (score={best_score:.2f})")
            else:
                print(f"  {num} → ❌ no match")
        else:
            print(f"  {num} → ❌ no DOI or title found")

    return mapping


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Inject Zotero CSL_CITATION field codes into any Word document."
    )
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--mapping', help='JSON mapping file (number→key)')
    parser.add_argument('--user-id', default='0')
    parser.add_argument('--bib', help='BibTeX file (for author-year text matching)')
    parser.add_argument('--no-bibliography', action='store_true',
                        help='Skip removing old bibliography and adding placeholder')
    parser.add_argument('--build-mapping', action='store_true',
                        help='Auto-build mapping from docx bibliography')

    args = parser.parse_args()

    # ── Build or load mapping ──
    if args.build_mapping:
        print("Building mapping from docx bibliography...")
        mapping = build_numeric_mapping(args.input, args.user_id)
        if not mapping:
            print("❌ No mappings could be built")
            sys.exit(1)
        mapping_path = Path(args.output).with_suffix('.mapping.json')
        with open(mapping_path, 'w') as f:
            json.dump(mapping, f, indent=2)
        print(f"Saved mapping ({len(mapping)} entries) to {mapping_path}")
    elif args.mapping:
        mapping_path = args.mapping
        with open(mapping_path) as f:
            raw = json.load(f)
        mapping = {}
        for k, v in raw.items():
            mapping[k] = v.get('zotero_key', v) if isinstance(v, dict) else v
        print(f"Loaded {len(mapping)} mappings from {mapping_path}")
    else:
        print("❌ Provide --mapping or --build-mapping")
        sys.exit(1)

    # ── Detect mode ──
    first_key = next(iter(mapping))
    is_numeric = first_key.isdigit()
    mode = 'numeric' if is_numeric else 'author-year'
    print(f"Mode: {mode} ({'numbered citations' if is_numeric else 'author-year text'})")

    # ── Inject ──
    print(f"Opening {args.input} ...")
    doc = Document(args.input)
    body = doc.element.body

    if is_numeric:
        total, warnings = inject_numeric(body, mapping, args.user_id)
    else:
        total, warnings = inject_author_year_text(body, mapping, args.user_id, args.bib)

    # ── Post-processing ──
    if not args.no_bibliography:
        remove_references_entries(body)
        add_zotero_bibliography(body)
    else:
        print("  --no-bibliography: skip bibliography handling")

    ensure_zotero_style(doc)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output))

    print(f"\n{'='*50}")
    print(f"Result: {total} citations injected, {len(warnings)} warnings")
    if warnings:
        for w in warnings[:10]:
            print(f"  ⚠ {w}")
    print(f"Open '{output}' in Word with Zotero plugin to refresh bibliography.")

    if total == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
