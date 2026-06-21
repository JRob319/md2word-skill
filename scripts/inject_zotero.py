#!/usr/bin/env python3
"""
inject_zotero.py — Replace static citations in a Word document
with Zotero ADDIN CSL_CITATION field codes.

Auto-detects citation format from CSL file:
  - author-date → match (Author, Year) text patterns
  - numeric     → match superscript/bracket number patterns
  - note        → match footnote reference patterns

Usage:
    python3 inject_zotero.py \
        --input /tmp/pandoc_output.docx \
        --output final_zotero.docx \
        --mapping /tmp/citation_mapping.json \
        --csl ~/.claude/skills/md2word-skill/styles/institute-of-physics-harvard.csl \
        --bib references.bib \
        --user-id 6653483

Mapping JSON format (numeric mode):
    {"1": "J8K6WXE8", "2": "7SU3QAU9", ...}
    Keys are pandoc citation numbers, values are Zotero item keys.

Mapping JSON format (author-date mode):
    {"kendall2017uncertainties": "J8K6WXE8", "gal2016dropout": "7SU3QAU9", ...}
    Keys are cite_keys, values are Zotero item keys.
"""

import argparse
import json
import os
import random
import re
import string
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from lxml import etree
from docx import Document
from docx.oxml.ns import qn


# ── CSL format detection ─────────────────────────────────────────────

def detect_csl_format(csl_path):
    """Read citation-format from CSL XML. Returns 'author-date', 'numeric', 'note', or 'label'."""
    tree = ET.parse(csl_path)
    root = tree.getroot()
    ns = {'csl': 'http://purl.org/net/xbiblio/csl'}
    for cat in root.findall('.//csl:category', ns):
        fmt = cat.get('citation-format')
        if fmt:
            return fmt
    # Check dependent style: follow independent-parent link
    for link in root.findall('.//{http://purl.org/net/xbiblio/csl}link'):
        if link.get('rel') == 'independent-parent':
            parent_href = link.get('href', '')
            parent_name = parent_href.split('/')[-1]
            styles_dir = os.path.dirname(csl_path)
            parent_path = os.path.join(styles_dir, f'{parent_name}.csl')
            if os.path.isfile(parent_path):
                return detect_csl_format(parent_path)
    return 'author-date'  # default


# ── Helpers ───────────────────────────────────────────────────────────

def random_citation_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))


def build_csl_citation(item_key, user_id, cite_key=None):
    """Build a single-item CSL_CITATION JSON payload."""
    uri = f"http://zotero.org/users/local/{user_id}/items/{item_key}"
    citation_items = [{"uris": [uri]}]
    return json.dumps({
        "citationID": random_citation_id(),
        "properties": {"noteIndex": 0},
        "citationItems": citation_items,
        "schema": "https://github.com/citation-style-language/schema/raw/master/csl-citation.json"
    }, ensure_ascii=False)


def build_csl_citation_multi(items, user_id):
    """Build a multi-item CSL_CITATION JSON payload for grouped citations."""
    citation_items = []
    for item_key in items:
        uri = f"http://zotero.org/users/local/{user_id}/items/{item_key}"
        citation_items.append({"uris": [uri]})
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
    """
    Return list of <w:r> elements forming a Zotero ADDIN CSL_CITATION field.
    display_text: the text shown in the document (e.g. "(Kendall and Gal 2017)" or "1")
    """
    instr_text = f" ADDIN ZOTERO_ITEM CSL_CITATION {csl_json} "

    runs = []

    # 1. fldChar begin
    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:fldChar w:fldCharType="begin"/>'
        '</w:r>'
    ))

    # 2. instrText
    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:instrText xml:space="preserve">{}</w:instrText>'
        '</w:r>'.format(escape_xml(instr_text))
    ))

    # 3. fldChar separate
    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:fldChar w:fldCharType="separate"/>'
        '</w:r>'
    ))

    # 4. Display text
    if rPr_xml:
        display_rpr = rPr_xml.replace(
            '</w:rPr>',
            '<w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        )
        display_xml = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'{display_rpr}'
            f'<w:t xml:space="preserve">{escape_xml(display_text)}</w:t>'
            '</w:r>'
        )
    elif superscript:
        display_xml = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:rPr><w:rStyle w:val="ZoteroCitation"/>'
            '<w:vertAlign w:val="superscript"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape_xml(display_text)}</w:t>'
            '</w:r>'
        )
    else:
        display_xml = (
            '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape_xml(display_text)}</w:t>'
            '</w:r>'
        )
    runs.append(make_run(display_xml))

    # 5. fldChar end
    runs.append(make_run(
        '<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:rPr><w:rStyle w:val="ZoteroCitation"/></w:rPr>'
        '<w:fldChar w:fldCharType="end"/>'
        '</w:r>'
    ))

    return runs


# ── Numeric mode ──────────────────────────────────────────────────────

NUMERIC_RE = re.compile(r'^(\d+(?:\s*,\s*\d+)*)$')


def is_superscript_citation_run(r_elem):
    """Check if a <w:r> is a superscript citation (digits + commas only)."""
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


def inject_numeric(body, citation_map, user_id):
    """Replace superscript numbered citations with Zotero fields."""
    total = 0
    warnings = []

    for p_elem in body.iter(qn('w:p')):
        runs_to_replace = []
        for r_elem in list(p_elem):
            if is_superscript_citation_run(r_elem):
                runs_to_replace.append(r_elem)

        for r_elem in runs_to_replace:
            t_elem = r_elem.find(qn('w:t'))
            text = t_elem.text.strip()
            nums = [int(n.strip()) for n in text.split(',')]

            rPr_xml = etree.tostring(r_elem.find(qn('w:rPr')), encoding='unicode') if r_elem.find(qn('w:rPr')) is not None else None

            item_keys = []
            for n in nums:
                key = citation_map.get(str(n))
                if key is None:
                    warnings.append(f"No Zotero key for citation #{n}")
                    break
                item_keys.append(key)
            else:
                # All found
                if len(item_keys) == 1:
                    csl_json = build_csl_citation(item_keys[0], user_id)
                else:
                    csl_json = build_csl_citation_multi(item_keys, user_id)
                display = ",".join(str(n) for n in nums)
                field_runs = create_zotero_field(display, csl_json, rPr_xml, superscript=True)

                parent = r_elem.getparent()
                idx = list(parent).index(r_elem)
                parent.remove(r_elem)
                for i, fr in enumerate(field_runs):
                    parent.insert(idx + i, fr)
                total += 1
                print(f"  ✓ Replaced numeric citation [{display}]")

    return total, warnings


# ── Author-year mode ──────────────────────────────────────────────────

def load_bib_lookup(bib_path):
    """Build cite_key → {authors: [lastName, ...], year: str} from BIB."""
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
    """
    Given citation text like "Kendall and Gal 2017" or "Isensee et al 2018",
    find matching cite_key(s) from bib_lookup.
    Returns list of cite_keys.
    """
    matches = []
    for cite_key, info in bib_lookup.items():
        year = info['year']
        authors = info['authors']

        # Check year present
        if year and year not in text:
            continue

        # Check at least one author name present
        for author in authors:
            # Handle names with spaces: "Van der Berg" → check whole name
            if len(author.split()) > 1:
                # Try last word first (most reliable)
                last_word = author.split()[-1]
                if last_word in text:
                    matches.append(cite_key)
                    break
            else:
                if author in text:
                    matches.append(cite_key)
                    break

    return matches


def inject_author_year(body, citation_map, user_id, bib_path=None):
    """替换 author-year 引用为 Zotero field。"""
    bib_lookup = load_bib_lookup(bib_path) if bib_path else {}
    has_anchor = any((hl.get(qn('w:anchor'), '') or '').startswith('ref-')
                     for hl in body.iter(qn('w:hyperlink')))
    if has_anchor:
        print("  模式: hyperlink anchor (pandoc link-citations:true → 精确)")
        return _inject_author_year_anchor(body, citation_map, user_id, bib_lookup)
    print("  模式: 文本匹配 fallback（建议 pandoc 加 -M link-citations=true 以精确区分同年同作者）")
    return _inject_author_year_text(body, citation_map, user_id, bib_path)


def _hl_to_cite_key(hl_elem, citation_map, bib_lookup):
    """从 hyperlink 元素解析 cite_key。
    优先用 ref-* anchor（精确）；fallback 用 hyperlink 文本反查 bib（处理组合引用第2+项的哈希 anchor）。
    """
    anchor = hl_elem.get(qn('w:anchor'), '') or ''
    if anchor.startswith('ref-'):
        return anchor[4:]
    # 哈希 anchor：用文本内容（如 "Karaoz and Brodie 2022"）反查 bib
    text = ''.join(t.text for t in hl_elem.iter(qn('w:t')) if t.text)
    matches = match_author_year_text(text, bib_lookup) if bib_lookup else []
    return matches[0] if matches else None


def _inject_author_year_anchor(body, citation_map, user_id, bib_lookup=None):
    """anchor 模式：处理三种 pandoc 输出结构：
    1. \\citep 单项:   run('(') + hyperlink(ref-*) + run(')')
    2. \\citep 组合:   run('(') + hyperlink(ref-*) + run(',') + hyperlink(hash) + ... + run(')')
    3. \\citet:        run('Author') + run('et al') + run('(') + hyperlink(ref-*，只含年份) + run(')')
       → 需向前收集作者 run，合并成完整 field
    """
    total, warnings = 0, []
    for p_elem in body.iter(qn('w:p')):
        children = list(p_elem)
        consumed = set()

        for idx, child in enumerate(children):
            if id(child) in consumed:
                continue

            # ── 情况 1 & 2：run 含 '(' 开启 citep 组 ──
            if child.tag == qn('w:r'):
                t_elem = child.find(qn('w:t'))
                if not (t_elem is not None and t_elem.text and '(' in t_elem.text):
                    continue
                cite_keys, elems, closing_run, j = [], [], None, idx + 1
                while j < len(children):
                    if id(children[j]) in consumed:
                        j += 1; continue
                    c = children[j]
                    if c.tag == qn('w:hyperlink'):
                        ck = _hl_to_cite_key(c, citation_map, bib_lookup)
                        if ck:
                            cite_keys.append(ck)
                        elems.append(c); j += 1
                    elif c.tag == qn('w:r'):
                        t = c.find(qn('w:t'))
                        txt = (t.text or '') if t is not None else ''
                        if ')' in txt:
                            closing_run = c; break
                        elif txt.strip() in ('', ',', ';'):
                            elems.append(c); j += 1
                        else:
                            break
                    else:
                        break
                if not (cite_keys and closing_run is not None):
                    continue
                # 向前收集 \citet 的作者 run（紧邻 '(' run 之前的纯文本 run）
                author_runs = []
                k = idx - 1
                while k >= 0 and id(children[k]) not in consumed:
                    c = children[k]
                    if c.tag != qn('w:r'):
                        break
                    t = c.find(qn('w:t'))
                    txt = (t.text or '').strip() if t is not None else ''
                    if txt == '' or re.match(r'^[A-Za-zÀ-ÿ\-\s\.]+$', txt):
                        author_runs.insert(0, c)
                        k -= 1
                    else:
                        break

                item_keys = []
                for ck in cite_keys:
                    ik = citation_map.get(ck)
                    if ik:
                        item_keys.append(ik)
                    else:
                        warnings.append(f"No Zotero key for cite_key: {ck}")
                if not item_keys:
                    continue

                hl_texts = [''.join(t.text for t in hl.iter(qn('w:t')) if t.text)
                            for hl in elems if hl.tag == qn('w:hyperlink')]
                author_prefix = ''.join(
                    (c.find(qn('w:t')).text or '') for c in author_runs
                    if c.find(qn('w:t')) is not None
                ).rstrip()
                if author_prefix:
                    # \citet: "Author et al (Year)" → display = "Author et al (Year)"
                    display = author_prefix + ' (' + ', '.join(hl_texts) + ')'
                else:
                    display = '(' + ', '.join(hl_texts) + ')'

                csl_json = (build_csl_citation(item_keys[0], user_id) if len(item_keys) == 1
                            else build_csl_citation_multi(item_keys, user_id))
                field_runs = create_zotero_field(display, csl_json, superscript=False)

                elems_to_remove = author_runs + [child] + elems + [closing_run]
                parent = child.getparent()
                # 插入位置：author_runs 存在时从 author_runs[0] 前，否则从 child 前
                anchor_elem = author_runs[0] if author_runs else child
                pos = list(parent).index(anchor_elem)
                for elem in elems_to_remove:
                    if elem.getparent() is not None:
                        parent.remove(elem)
                for fi, fr in enumerate(field_runs):
                    parent.insert(pos + fi, fr)
                for elem in elems_to_remove:
                    consumed.add(id(elem))
                total += 1
                print(f"  ✓ Replaced: {display} ({len(item_keys)} items)")

    return total, warnings


def _inject_author_year_text(body, citation_map, user_id, bib_path=None):
    """文本匹配 fallback：pandoc 默认纯文本 (Author Year)，无 hyperlink。合并跨 run 引用
    文本，用 bib author/year 反查 cite_key。同年同作者不精确（会全匹配上）。"""
    if not bib_path:
        print("⚠ 文本匹配需要 --bib")
        return 0, ["missing --bib"]
    bib_lookup = load_bib_lookup(bib_path)
    total, warnings = 0, []
    for p_elem in body.iter(qn('w:p')):
        runs = list(p_elem.findall(qn('w:r')))
        consumed = set()
        for idx, r in enumerate(runs):
            if id(r) in consumed: continue
            t = r.find(qn('w:t'))
            txt = t.text if (t is not None and t.text) else ''
            if '(' not in txt: continue
            group, gtext = [r], txt
            k = idx + 1
            while ')' not in gtext and k < len(runs):
                if id(runs[k]) in consumed: break
                group.append(runs[k])
                nt = runs[k].find(qn('w:t'))
                if nt is not None and nt.text: gtext += nt.text
                k += 1
            if ')' not in gtext: continue
            cites = re.findall(r'\(([^)]+)\)', gtext)
            if not cites: continue
            cite_keys = []
            for c in cites: cite_keys.extend(match_author_year_text(c, bib_lookup))
            item_keys = [citation_map.get(ck) for ck in cite_keys if citation_map.get(ck)]
            for ck in cite_keys:
                if not citation_map.get(ck): warnings.append(f"No Zotero key for cite_key: {ck}")
            if not item_keys: continue
            csl_json = (build_csl_citation(item_keys[0], user_id) if len(item_keys) == 1
                        else build_csl_citation_multi(item_keys, user_id))
            display = '(' + '; '.join(cites) + ')'
            field_runs = create_zotero_field(display, csl_json, superscript=False)
            parent = r.getparent()
            pos = list(parent).index(group[0])
            for fr in field_runs: parent.insert(pos, fr); pos += 1
            for gr in group:
                consumed.add(id(gr))
                if gr.getparent() is not None: parent.remove(gr)
            total += 1
            print(f"  ✓ Replaced: {display} ({len(item_keys)} items)")
    return total, warnings


# ── Bibliography + style injection (shared) ───────────────────────────

def remove_references_section(body):
    """Remove the static References section.
    策略1: 找 'References' 标题，删除其后所有内容（MD 输入）。
    策略2: 找 Bibliography 样式段落，删除所有连续段（LaTeX/pandoc 输入，无标题）。
    """
    # 策略1: heading
    refs_heading = None
    for p_elem in body.iter(qn('w:p')):
        if ''.join(t.text for t in p_elem.iter(qn('w:t')) if t.text).strip() == 'References':
            refs_heading = p_elem
            break
    if refs_heading is not None:
        elems_to_remove = []
        found = False
        for child in list(body):
            if child is refs_heading:
                found = True
            if found:
                elems_to_remove.append(child)
        for elem in elems_to_remove:
            body.remove(elem)
        print(f"Removed {len(elems_to_remove)} elements from References section")
        return len(elems_to_remove)

    # 策略2: Bibliography 样式段落
    bib_paras = []
    for p_elem in body:
        if p_elem.tag != qn('w:p'):
            continue
        pPr = p_elem.find(qn('w:pPr'))
        style = ''
        if pPr is not None:
            ps = pPr.find(qn('w:pStyle'))
            if ps is not None:
                style = ps.get(qn('w:val'), '')
        if style == 'Bibliography':
            bib_paras.append(p_elem)

    if bib_paras:
        for elem in bib_paras:
            body.remove(elem)
        print(f"Removed {len(bib_paras)} Bibliography paragraphs")
        return len(bib_paras)

    print("⚠ No References section found — skipping removal")
    return 0


def add_bibliography_placeholder(body):
    """Add References heading + ZOTERO_BIBLIOGRAPH field."""
    # Heading
    ref_heading = etree.SubElement(body, qn('w:p'))
    ref_heading.set(qn('w:rsidR'), '00000000')
    ref_heading.set(qn('w:rsidRDefault'), '00000000')
    pPr = etree.SubElement(ref_heading, qn('w:pPr'))
    pStyle = etree.SubElement(pPr, qn('w:pStyle'))
    pStyle.set(qn('w:val'), 'Heading1')
    r_text = etree.SubElement(ref_heading, qn('w:r'))
    t = etree.SubElement(r_text, qn('w:t'))
    t.text = 'References'

    # Bibliography field
    bib_para = etree.SubElement(body, qn('w:p'))
    bib_para.set(qn('w:rsidR'), '00000000')
    bib_para.set(qn('w:rsidRDefault'), '00000000')
    pPr2 = etree.SubElement(bib_para, qn('w:pPr'))
    pStyle2 = etree.SubElement(pPr2, qn('w:pStyle'))
    pStyle2.set(qn('w:val'), 'Bibliography')

    bib_json = json.dumps({
        "bibliographyStyle": "http://www.zotero.org/styles/apa",
        "bibliographyDefaults": "",
        "citationCluster": []
    }, ensure_ascii=False)
    bib_instr = f" ADDIN ZOTERO_BIBLIOGRAPH {bib_json} "

    field_runs = [
        make_run('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:fldChar w:fldCharType="begin"/></w:r>'),
        make_run('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:instrText xml:space="preserve">{}</w:instrText>'
                 '</w:r>'.format(escape_xml(bib_instr))),
        make_run('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:fldChar w:fldCharType="separate"/></w:r>'),
        make_run('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:t xml:space="preserve">[BIBLIOGRAPHY]</w:t></w:r>'),
        make_run('<w:r xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:fldChar w:fldCharType="end"/></w:r>'),
    ]
    for r in field_runs:
        bib_para.append(r)
    print("Added bibliography placeholder")


def ensure_zotero_style(doc):
    """Add ZoteroCitation character style if missing."""
    styles_part = doc.styles.element
    for style in styles_part.iter(qn('w:style')):
        if style.get(qn('w:styleId')) == 'ZoteroCitation':
            return
    zotero_style = make_run(
        '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' w:type="character" w:styleId="ZoteroCitation">'
        '<w:name w:val="ZoteroCitation"/>'
        '<w:rPr>'
        '<w:vertAlign w:val="superscript"/>'
        '</w:rPr>'
        '</w:style>'
    )
    styles_part.append(zotero_style)
    print("Added ZoteroCitation character style")


# ── Main ──────────────────────────────────────────────────────────────

def inject_zotero_fields(input_path, output_path, mapping_path, user_id,
                         csl_path=None, bib_path=None):
    """Main injection logic — auto-selects mode from CSL."""

    # Detect format from CSL
    cite_format = 'numeric'  # default fallback
    if csl_path:
        cite_format = detect_csl_format(csl_path)
        print(f"CSL format: {cite_format} (from {os.path.basename(csl_path)})")

    # Load mapping（兼容新旧格式：新 {ck:{zotero_key,confidence,...}} / 旧 {ck:key}）
    with open(mapping_path) as f:
        raw = json.load(f)
    citation_map, low_conf = {}, []
    for k, v in raw.items():
        if isinstance(v, dict):
            citation_map[k] = v.get("zotero_key", "")
            if v.get("confidence") and v["confidence"] != "high":
                low_conf.append(k)
        else:
            citation_map[k] = v
    print(f"Loaded {len(citation_map)} mappings" + (f" ({len(low_conf)} low-confidence: {low_conf})" if low_conf else ""))

    # author-date mode: mapping keys should be cite_keys
    # If mapping is numeric, we can't do hyperlink-based matching
    # User should provide cite_key mapping from Step 4
    first_key = next(iter(citation_map))
    if first_key.isdigit():
        print("⚠ Mapping uses numeric keys, but author-date mode needs cite_key mapping.")
        print("  Re-run Step 4 with --output-format cite_key, or provide a cite_key→zotero_key JSON.")

    print(f"Opening {input_path} ...")
    doc = Document(str(input_path))
    body = doc.element.body

    # Inject based on format
    if cite_format == 'author-date':
        total, warnings = inject_author_year(body, citation_map, user_id, bib_path)
    elif cite_format == 'numeric':
        total, warnings = inject_numeric(body, citation_map, user_id)
    else:
        # note, label — fall back to numeric
        print(f"⚠ Format '{cite_format}' not fully supported, trying numeric mode")
        total, warnings = inject_numeric(body, citation_map, user_id)

    # Post-processing
    remove_references_section(body)
    add_bibliography_placeholder(body)
    ensure_zotero_style(doc)

    # Save
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving to {output} ...")
    doc.save(str(output))

    print(f"\n{'='*50}")
    print(f"Result: {total} citations injected, {len(warnings)} warnings")
    if warnings:
        for w in warnings[:10]:
            print(f"  ⚠ {w}")
    print(f"Open '{output}' in Word with Zotero plugin to refresh bibliography.")

    return total, len(warnings)


def main():
    parser = argparse.ArgumentParser(
        description="Inject Zotero CSL_CITATION field codes into Word. Auto-detects citation format from CSL."
    )
    parser.add_argument('--input', required=True, help='Input Word file')
    parser.add_argument('--output', required=True, help='Output Word file')
    parser.add_argument('--mapping', required=True, help='JSON mapping (number→key or cite_key→key)')
    parser.add_argument('--csl', required=False, help='CSL style file (auto-detects format)')
    parser.add_argument('--bib', required=False, help='BibTeX file (required for author-date mode)')
    parser.add_argument('--user-id', default='0', help='Zotero user ID (default: 0 for local)')

    args = parser.parse_args()

    total, warnings = inject_zotero_fields(
        input_path=args.input,
        output_path=args.output,
        mapping_path=args.mapping,
        user_id=args.user_id,
        csl_path=args.csl,
        bib_path=args.bib,
    )

    if total == 0:
        print("\n⚠ No citations were replaced.")
        sys.exit(1)


if __name__ == "__main__":
    main()
