#!/usr/bin/env python3
"""
MD ↔ BIB 交叉验证 + 三源文献真实性核查与元数据裁决

三源核查（PubMed 独立策展，裁决时加权）:
  - CrossRef  (api.crossref.org)        : DOI 锚点 / 期刊 / 卷期页 / 年份（出版商直供）
  - PubMed   (eutils.ncbi.nlm.nih.gov) : 生物医学金标准 / 作者全名最规范（NLM 人工索引）
  - OpenAlex (api.openalex.org)        : 覆盖最广 / 作者机构 / ORCID（免费无 key）

⚠️ 源并不独立：OpenAlex 大量数据继承自 CrossRef，故 "CrossRef+OpenAlex 一致" ≠ 双重
   独立证据。PubMed 是独立策展，其单票分量 ≥ CR+OA 两票 —— 裁决不简单数人头。

裁决流程: 归一化消假冲突 → 判定同篇 → AUTO/FLAG/REJECT 三档 → 真冲突按「字段最优源」取值

档位:
  AUTO    一致 / 归一化后一致 / 多数票明确          → 自动采用 (PASS)
  FLAG    实质冲突但有合理默认                        → 导入(用最优值) + 标记冲突
  REJECT  根本不像同一篇 / 关键字段三源全冲突         → 不导入
  SKIP    三源均未找到                               → 不导入（真实性未验证）

用法:
  python3 verify_references.py <md_path> <bib_path> [--verify] [--strict] [--json OUT]
  --verify   启用三源核查（默认只做 MD↔BIB 交叉验证）
  --strict   FLAG 也阻塞（默认 FLAG 不阻塞：导入但标记到 Extra）
"""

import argparse
import json
import re
import subprocess
import sys
import os
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed


# ── 常量 ──────────────────────────────────────────────────────────────
PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENALEX_BASE = "https://api.openalex.org"
CONTACT_EMAIL = os.environ.get("NCBI_EMAIL", "md2word-skill@example.com")

# 冲突时各字段取值的源优先级（PubMed 对作者字段加权排前）
SOURCE_PRIORITY = {
    "title":   ["crossref", "pubmed", "openalex"],
    "year":    ["crossref", "pubmed", "openalex"],
    "authors": ["pubmed", "crossref", "openalex"],   # 作者优先 PubMed（NLM 策展最规范）
    "journal": ["crossref", "pubmed", "openalex"],
    "volume":  ["crossref", "pubmed", "openalex"],
    "issue":   ["crossref", "pubmed", "openalex"],
    "pages":   ["crossref", "pubmed", "openalex"],
}

# PubMed 免费档限速 3 req/s —— 并发核查时礼貌等待
PUBMED_PAUSE = 0.4
MAX_WORKERS = 4


# ── BibTeX / Markdown 解析（沿用原版） ────────────────────────────────
def load_bib(bib_path):
    """解析标准 BibTeX 文件，返回 {cite_key: {doi, title, year, author}}"""
    import bibtexparser
    with open(bib_path, encoding="utf-8") as f:
        db = bibtexparser.load(f)
    entries = {}
    for e in db.entries:
        entries[e["ID"]] = {
            "doi": e.get("doi", "").strip().lstrip("DOI:").lower() or None,
            "title": e.get("title", "").strip().strip("{}") or None,
            "year": e.get("year", "").strip() or None,
            "author": e.get("author", "").strip() or None,
        }
    return entries


def _ast_citation_ids(obj):
    """递归遍历 pandoc AST，按出现顺序收集所有 Cite 节点的 citationId（含组合引用各项）。"""
    ids = []
    if isinstance(obj, dict):
        if obj.get("t") == "Cite" and isinstance(obj.get("c"), list) and obj["c"]:
            for cit in obj["c"][0]:              # c[0] = citations list
                if isinstance(cit, dict) and cit.get("citationId"):
                    ids.append(cit["citationId"])
        for v in obj.values():
            ids.extend(_ast_citation_ids(v))
    elif isinstance(obj, list):
        for item in obj:
            ids.extend(_ast_citation_ids(item))
    return ids


def extract_md_keys(md_path):
    """提取 MD、LaTeX 或 Typst 中所有引用的 cite_key，按出现顺序去重。

    MD/Typst: 主路径 pandoc AST（Cite.citationId），fallback 正则 @key / [@key]
    LaTeX:    主路径 pandoc AST，fallback 正则 \\cite{}/\\citep{} 等
    """
    ext = os.path.splitext(md_path)[1].lower()
    keys = []
    try:
        r = subprocess.run(["pandoc", md_path, "-t", "json"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            keys = _ast_citation_ids(json.loads(r.stdout))
    except Exception:
        keys = []
    if not keys:  # pandoc 失败 → 正则 fallback
        with open(md_path, encoding="utf-8") as f:
            text = f.read()
        if ext == ".tex":
            for block in re.findall(r"\\cite\w*\*?(?:\[[^\]]*\]){0,2}\{([^}]+)\}", text):
                for part in re.split(r",", block):
                    part = part.strip()
                    if part:
                        keys.append(part)
        else:
            # MD 和 Typst 都用 @key 语法
            for block in re.findall(r"\[@([^\]]+)\]", text):
                for part in re.split(r"[;,]", block):
                    part = part.strip().lstrip("-")
                    m = re.match(r"@?([\w][\w-]*)", part)
                    if m:
                        keys.append(m.group(1))
            # Typst 裸 @key（不在 [] 内）
            if ext == ".typ":
                for m in re.finditer(r"(?<!\[)@([\w][\w-]*)", text):
                    keys.append(m.group(1))
    seen = {}
    for k in keys:
        if k not in seen:
            seen[k] = len(seen) + 1
    return seen


def cross_validate(md_path, bib_path):
    """MD ↔ BIB 交叉验证"""
    print(f"  解析 MD 引用: {md_path}", flush=True)
    md_keys = extract_md_keys(md_path)
    print(f"  找到 {len(md_keys)} 个唯一引用", flush=True)

    print(f"  加载 BIB: {bib_path}", flush=True)
    bib_entries = load_bib(bib_path)
    bib_keys = set(bib_entries.keys())
    print(f"  找到 {len(bib_entries)} 个条目", flush=True)

    missing_in_bib = sorted(set(md_keys.keys()) - bib_keys)
    unused_in_md = sorted(bib_keys - set(md_keys.keys()))
    matched = set(md_keys.keys()) & bib_keys
    no_doi = sorted(k for k in matched if not bib_entries[k].get("doi"))
    no_title = sorted(k for k in matched if not bib_entries[k].get("title"))

    print(f"  ✅ 匹配 {len(matched)} | ❌ BIB缺失 {len(missing_in_bib)} | ⚠ MD未引用 {len(unused_in_md)}", flush=True)
    return {
        "md_total": len(md_keys),
        "bib_total": len(bib_entries),
        "matched": len(matched),
        "missing_in_bib": missing_in_bib,
        "unused_in_md": unused_in_md,
        "no_doi": no_doi,
        "no_title": no_title,
    }


def print_cross_report(result):
    print("\n交叉验证报告")
    print("━" * 50)
    print(f'  MD 引用数: {result["md_total"]}    BIB 条目数: {result["bib_total"]}')
    print(f'  ✅ 匹配: {result["matched"]}')
    if result["missing_in_bib"]:
        print(f'\n❌ MD 引用了但 BIB 缺少 ({len(result["missing_in_bib"])}):  ← 必须修复')
        for k in result["missing_in_bib"]:
            print(f"   - {k}")
    if result["unused_in_md"]:
        print(f'\n⚠️  BIB 有但 MD 未引用 ({len(result["unused_in_md"])}):  ← 可选清理')
        for k in result["unused_in_md"][:10]:
            print(f"   - {k}")
        if len(result["unused_in_md"]) > 10:
            print(f"   ... 等 {len(result['unused_in_md'])} 条")
    if result["no_doi"]:
        print(f'\nℹ️  无 DOI 走标题反查 ({len(result["no_doi"])}/{result["matched"]}):  ← 三源 title 查询')
    if result["no_title"]:
        print(f'\n❌ 无 title 无法反查 ({len(result["no_title"])}):  ← 必须修复')
        for k in result["no_title"]:
            print(f"   - {k}")
    print("━" * 50)


# ── 归一化（消假冲突） ────────────────────────────────────────────────
def normalize(text):
    """去标点+小写，用于标题比较"""
    return re.sub(r"[^\w]", "", text or "").lower()


def norm_lastname(name):
    """作者姓氏归一化: 去变音符/连字符/句点/空格 + 小写。Fogliatà ≡ Fogliata ≡ FOGLIATA"""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[.\-'\s]", "", n).lower()


def norm_year(y):
    m = re.search(r"\d{4}", str(y or ""))
    return m.group(0) if m else ""


def _title_similar(a, b):
    """标题词级 Jaccard 相似度 ≥ 0.8 视为同一篇"""
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.8


# ── 三源查询 ──────────────────────────────────────────────────────────
def query_crossref(doi=None, title=None):
    """查询 CrossRef，返回标准化权威元数据 dict 或 None"""
    try:
        if doi:
            r = subprocess.run(
                ["curl", "-s", f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}"],
                capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                msg = json.loads(r.stdout).get("message", {})
                if msg:
                    return _parse_crossref(msg)
        elif title:
            q = urllib.parse.quote(title)
            r = subprocess.run(
                ["curl", "-s", f"https://api.crossref.org/works?query.title={q}&rows=1"],
                capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                items = json.loads(r.stdout).get("message", {}).get("items", [])
                if items:
                    return _parse_crossref(items[0])
        return None
    except Exception:
        return None


def _parse_crossref(msg):
    dp = msg.get("published-print", msg.get("published-online", {})).get("date-parts", [[None]])[0]
    authors = [{"family": a.get("family", ""), "given": a.get("given", "")}
               for a in msg.get("author", [])]
    return {
        "title": (msg.get("title") or [""])[0],
        "year": str(dp[0]) if dp and dp[0] else None,
        "authors": authors,
        "journal": (msg.get("container-title") or [""])[0],
        "volume": str(msg.get("volume", "")) or None,
        "issue": str(msg.get("issue", "")) or None,
        "pages": msg.get("page", "") or None,
        "doi": msg.get("DOI", ""),
        "issn": (msg.get("ISSN") or [""])[0] or None,
    }


def query_pubmed(doi=None, title=None):
    """查询 PubMed。DOI 用 idconv 精确查（避免 esearch [AID] 把无效 DOI 当文本模糊
    误匹配到无关论文）；title 用 esearch [TI]。esummary 拿元数据。"""
    try:
        pmid = None
        if doi:
            # idconv 精确: DOI → PMID。找不到返回 None（不模糊匹配）
            url = (f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={urllib.parse.quote(doi)}"
                   f"&format=json&tool=md2word&email={CONTACT_EMAIL}")
            r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                recs = json.loads(r.stdout).get("records", [])
                if recs and recs[0].get("status") != "error":
                    pmid = recs[0].get("pmid")
        elif title:
            esearch = (f"{PUBMED_BASE}/esearch.fcgi?db=pubmed&term={urllib.parse.quote(title + '[TI]')}"
                       f"&retmode=json&retmax=1&tool=md2word&email={CONTACT_EMAIL}")
            r = subprocess.run(["curl", "-s", esearch], capture_output=True, text=True, timeout=15)
            if r.returncode == 0 and r.stdout.strip():
                idlist = json.loads(r.stdout).get("esearchresult", {}).get("idlist", [])
                if idlist:
                    pmid = idlist[0]
        if not pmid:
            return None
        time.sleep(PUBMED_PAUSE)  # 免费 3 req/s 礼貌限速
        esummary = (f"{PUBMED_BASE}/esummary.fcgi?db=pubmed&id={pmid}&retmode=json"
                    f"&tool=md2word&email={CONTACT_EMAIL}")
        r = subprocess.run(["curl", "-s", esummary], capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        rec = json.loads(r.stdout).get("result", {}).get(pmid, {})
        if not rec or "error" in rec:
            return None
        return _parse_pubmed(rec)
    except Exception:
        return None


def _parse_pubmed(rec):
    authors = []
    for a in rec.get("authors", []):
        # esummary author 只有小写 "name"（如 "Fogliata A" = LastName + Initials）
        name = a.get("name") or a.get("LastName", "") or ""
        parts = name.split()
        if len(parts) >= 2:
            last, given = parts[0], " ".join(parts[1:])   # PubMed: 姓在前，名缩写在后
        elif parts:
            last, given = parts[0], ""
        else:
            last, given = "", ""
        authors.append({"family": last, "given": given})
    doi = ""
    for el in rec.get("articleids", []):
        if el.get("idtype") == "doi":
            doi = (el.get("value") or "").lower()
    year = norm_year(rec.get("sortpubdate") or rec.get("pubdate") or "")
    return {
        "title": rec.get("title", "").rstrip("."),
        "year": year or None,
        "authors": authors,
        "journal": rec.get("fulljournalname", "") or rec.get("source", ""),
        "volume": rec.get("volume", "") or None,
        "issue": rec.get("issue", "") or None,
        "pages": rec.get("pages", "") or None,
        "doi": doi,
        "issn": rec.get("issn", "") or None,
        "pmid": rec.get("uid", ""),
    }


def query_openalex(doi=None, title=None):
    """查询 OpenAlex。by-DOI 直查；by-title search。"""
    try:
        if doi:
            url = f"{OPENALEX_BASE}/works/doi:{doi}?mailto={CONTACT_EMAIL}"
        elif title:
            url = (f"{OPENALEX_BASE}/works?search={urllib.parse.quote(title)}"
                   f"&per-page=1&mailto={CONTACT_EMAIL}")
        else:
            return None
        r = subprocess.run(["curl", "-s", url], capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        if isinstance(data, dict) and data.get("results") is not None:
            data = data["results"][0] if data["results"] else None
        if not data or not data.get("id"):
            return None
        return _parse_openalex(data)
    except Exception:
        return None


def _parse_openalex(work):
    authors = []
    for au in work.get("authorships", []):
        a = au.get("author", {}) or {}
        raw = au.get("raw_author_name") or a.get("display_name", "")
        if "," in raw:
            # "Last, Given" 格式（OpenAlex raw_author_name 常见）
            parts = [p.strip() for p in raw.split(",", 1)]
            last = parts[0]
            given = parts[1] if len(parts) > 1 else ""
        else:
            # "Given Last" 格式（display_name 常见）
            p = raw.rsplit(" ", 1)
            given, last = (p[0], p[1]) if len(p) == 2 else ("", raw)
        authors.append({
            "family": last,
            "given": given,
            "orcid": (a.get("orcid") or "").replace("https://orcid.org/", ""),
        })
    venue = (work.get("primary_location", {}) or {}).get("source", {}) or {}
    biblio = work.get("biblio", {}) or {}
    pages = f'{biblio.get("first", "")}-{biblio.get("last", "")}'.strip("-")
    return {
        "title": work.get("title", "") or "",
        "year": work.get("publication_date", "")[:4] or None,
        "authors": authors,
        "journal": venue.get("display_name", "") or "",
        "volume": str(biblio.get("volume", "")) or None,
        "issue": str(biblio.get("issue", "")) or None,
        "pages": pages or None,
        "doi": (work.get("doi") or "").replace("https://doi.org/", ""),
        "issn": venue.get("issn_l") or (venue.get("issn") or [""])[0] or None,
        "cited_by_count": work.get("cited_by_count", 0),
    }


# ── 裁决 ──────────────────────────────────────────────────────────────
def is_same_paper(records):
    """records: {source: meta or None}。判定查到的几条是否同一篇。"""
    got = {s: r for s, r in records.items() if r}
    if not got:
        return False
    titles = [normalize(r.get("title", "")) for r in got.values()]
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            if titles[i] and titles[j] and not _title_similar(titles[i], titles[j]):
                return False
    years = [norm_year(r.get("year")) for r in got.values() if norm_year(r.get("year"))]
    if len(years) >= 2:
        yint = [int(y) for y in years]
        if max(yint) - min(yint) > 1:  # ±1 容忍 epub/print 差异
            return False
    return True


def reconcile(records):
    """
    records: {source: meta or None}
    返回 (authoritative, conflicts):
      authoritative: 裁决后字段（每字段从最优源取，格式同 meta dict）
      conflicts: [{field, values:{source: 展示值}, chosen_source}, ...]
    """
    got = {s: r for s, r in records.items() if r}
    authoritative, conflicts = {}, []
    if not got:
        return authoritative, conflicts

    for field in ["title", "year", "authors", "journal", "volume", "issue", "pages", "doi"]:
        # ── journal 特判：issn 一致 或 去括号文本一致 → 视为同一期刊（不冲突）──
        if field == "journal":
            issns = {(r.get("issn") or "").lower().replace("-", "")
                     for r in got.values() if (r.get("issn") or "").lower().replace("-", "")}
            texts = {normalize(re.sub(r"\s*\([^)]*\)", "", r.get("journal", "")))
                     for r in got.values()}
            texts.discard("")
            same = (len(issns) == 1) or (len(texts) == 1)
            chosen_src = next((s for s in SOURCE_PRIORITY["journal"] if s in got), None)
            if chosen_src:
                authoritative[field] = got[chosen_src].get("journal")
            if not same:
                conflicts.append({"field": "journal",
                                  "values": {s: r.get("journal") for s, r in got.items()},
                                  "chosen_source": chosen_src})
            continue

        # ── 通用字段：归一化比较 ──
        vals = {}  # source -> (raw, norm)
        for s, r in got.items():
            raw = r.get(field)
            if field == "authors":
                norm = tuple(norm_lastname(a.get("family", "")) for a in (raw or []))
            elif field == "year":
                norm = norm_year(raw)
            elif field in ("volume", "issue"):
                norm = re.sub(r"\D", "", str(raw or ""))
            elif field == "pages":
                # 页码只比起始页（PubMed 常缩写 "2253-65" = "2253-2265"）
                norm = re.split(r"[-–]", str(raw or ""))[0].strip()
            else:
                norm = normalize(raw)
            vals[s] = (raw, norm)
        present = {s: v[1] for s, v in vals.items() if v[1]}
        if not present:
            continue

        distinct = {v for v in present.values() if v}
        chosen_src = next((s for s in SOURCE_PRIORITY.get(field, ["crossref", "pubmed", "openalex"])
                           if s in present), None)
        if chosen_src and vals[chosen_src][0] is not None:
            authoritative[field] = vals[chosen_src][0]
        if len(distinct) > 1:
            display = {}
            for s, v in vals.items():
                if not v[1]:
                    continue
                display[s] = ([a.get("family", "") for a in (v[0] or [])]
                              if field == "authors" else v[0])
            conflicts.append({"field": field, "values": display, "chosen_source": chosen_src})

    # OpenAlex 独有的 ORCID 并入（不算冲突）
    oa = got.get("openalex")
    if oa:
        orcids = {norm_lastname(a.get("family", "")): a.get("orcid")
                  for a in oa.get("authors", []) if a.get("orcid")}
        if orcids:
            authoritative["orcids"] = orcids
    return authoritative, conflicts


def verify_one(entry):
    """核查单条文献（三源串行查，由 multi_verify 并发调度）。
    entry: {cite_key, doi, title, ...} → (cite_key, result)"""
    cite_key = entry["cite_key"]
    doi = entry.get("doi")
    title = entry.get("title")

    records = {
        "crossref": query_crossref(doi=doi, title=title),
        "pubmed": query_pubmed(doi=doi, title=title),
        "openalex": query_openalex(doi=doi, title=title),
    }

    # 关键防护：每个源的 title 必须与 bib title 相似，否则该源匹配错了 —— 防止单源
    # 错误数据因"无其他源可比"而误 PASS（如 PubMed 把无效 DOI 模糊匹配到无关论文）。
    bib_t = normalize(title)
    if bib_t:
        for s in list(records):
            r = records[s]
            if r:
                src_t = normalize(r.get("title", ""))
                if src_t and not _title_similar(src_t, bib_t):
                    records[s] = None  # 剔除 title 与 bib 不符的源

    found = [s for s, r in records.items() if r]

    if not found:
        return cite_key, {"status": "SKIP", "authoritative": {}, "conflicts": [],
                          "sources_found": [], "issues": ["三源均未找到或 title 与 bib 不符（DOI 可能无效）"]}

    if not is_same_paper(records):
        return cite_key, {"status": "REJECT", "authoritative": {}, "conflicts": [],
                          "sources_found": found,
                          "issues": ["源之间不像同一篇文献（title/作者/年份不匹配）"]}

    authoritative, conflicts = reconcile(records)
    status = "FLAG" if conflicts else "PASS"
    return cite_key, {"status": status, "authoritative": authoritative,
                      "conflicts": conflicts, "sources_found": found, "issues": []}


def multi_verify(verify_entries):
    """三源并发核查。verify_entries: {cite_key: {doi,title,...}}"""
    items = list(verify_entries.items())
    total = len(items)
    print(f"  三源核查 {total} 条 (CrossRef + PubMed + OpenAlex, 并发) ...", flush=True)
    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(verify_one, {**v, "cite_key": k}): k for k, v in items}
        for fut in as_completed(futures):
            ck = futures[fut]
            try:
                k, res = fut.result()
                results[k] = res
                done += 1
                print(f'  [{done}/{total}] {k}: {res["status"]} '
                      f'(源={",".join(res["sources_found"]) or "无"} '
                      f'冲突={len(res["conflicts"])})', flush=True)
            except Exception as e:
                done += 1
                results[ck] = {"status": "SKIP", "authoritative": {}, "conflicts": [],
                               "sources_found": [], "issues": [str(e)]}
                print(f"  [{done}/{total}] {ck}: ERROR ({e})", flush=True)
    return results


def print_verify_report(results, strict=False):
    from collections import Counter
    counts = Counter(r["status"] for r in results.values())
    print("\n三源文献核查报告")
    print("━" * 60)
    print(f"  核查 {len(results)} 条 | " + " | ".join(
        f"{s}: {counts.get(s, 0)}" for s in ["PASS", "FLAG", "REJECT", "SKIP"]))

    rejects = [(k, v) for k, v in results.items() if v["status"] == "REJECT"]
    flags = [(k, v) for k, v in results.items() if v["status"] == "FLAG"]
    skips = [(k, v) for k, v in results.items() if v["status"] == "SKIP"]

    if rejects:
        print(f"\n❌ REJECT ({len(rejects)}): 不导入")
        for k, v in rejects:
            print(f'   - {k}: {"; ".join(v["issues"])}')
    if flags:
        tag = "阻塞（--strict）" if strict else "导入但标记到 Extra"
        print(f"\n⚠️  FLAG ({len(flags)}): {tag}")
        for k, v in flags:
            for c in v["conflicts"]:
                vals = " | ".join(f"{s}={x}" for s, x in c["values"].items())
                print(f'   - {k}.{c["field"]}: {vals}  → 取 {c["chosen_source"]}')
    if skips:
        print(f"\n⏭  SKIP ({len(skips)}): 三源未找到")
        for k, v in skips:
            print(f"   - {k}")
    print("━" * 60)


# ── Main ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="MD↔BIB 交叉验证 + 三源(CrossRef/PubMed/OpenAlex)核查与元数据裁决")
    parser.add_argument("md_path", help="Markdown 文件路径")
    parser.add_argument("bib_path", help="BibTeX 文件路径")
    parser.add_argument("--verify", action="store_true", help="启用三源核查（默认只交叉验证）")
    parser.add_argument("--strict", action="store_true",
                        help="FLAG 也阻塞（默认 FLAG 不阻塞：导入但标记到 Extra）")
    parser.add_argument("--json", help="输出结果 JSON（含裁决后的权威元数据）")
    args = parser.parse_args()

    # Step 2b: 交叉验证
    print("Step 2b: MD ↔ BIB 交叉验证")
    cv_result = cross_validate(args.md_path, args.bib_path)
    print_cross_report(cv_result)

    fatal = cv_result["missing_in_bib"] + cv_result["no_title"]
    if fatal:
        print("\n⛔ 存在致命问题，请修复后重新运行。")
        if args.json:
            with open(args.json, "w") as f:
                json.dump({"cross_validate": cv_result}, f, ensure_ascii=False, indent=2)
        return 1

    # Step 2c: 三源核查（可选）
    v_results = None
    blocked = False
    if args.verify:
        print("\nStep 2c: 三源核查 (CrossRef + PubMed + OpenAlex)")
        bib_entries = load_bib(args.bib_path)
        md_keys = extract_md_keys(args.md_path)
        entries = {k: v for k, v in bib_entries.items() if k in md_keys}
        v_results = multi_verify(entries)
        print_verify_report(v_results, strict=args.strict)
        if args.strict and any(r["status"] in ("FLAG", "REJECT") for r in v_results.values()):
            blocked = True
            print("\n⛔ --strict 模式: 存在 FLAG/REJECT，已阻塞。去掉 --strict 可导入（FLAG 会标记到 Extra）。")

    # 输出 JSON（含权威元数据，供 import_zotero.py 使用）
    if args.json:
        output = {"cross_validate": cv_result}
        if v_results:
            output["multi_verify"] = v_results
        with open(args.json, "w") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n结果（含权威元数据）已保存到 {args.json}")

    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
