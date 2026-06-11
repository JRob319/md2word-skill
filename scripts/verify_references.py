#!/usr/bin/env python3
"""
MD ↔ BIB 交叉验证 + 双来源文献真实性核查

用法:
  python3 verify_references.py <md_path> <bib_path> [--verify] [--s2-script PATH]

选项:
  --verify       启用双来源文献真实性核查 (S2 + CrossRef)，默认只做交叉验证
  --s2-script    s2_api.py 的路径，默认自动查找
"""
import argparse
import json
import re
import subprocess
import sys
import os
import time


def load_bib(bib_path):
    """解析标准 BibTeX 文件，返回 {cite_key: {doi, title, year, author}}"""
    import bibtexparser
    with open(bib_path, encoding='utf-8') as f:
        db = bibtexparser.load(f)
    entries = {}
    for e in db.entries:
        entries[e['ID']] = {
            'doi': e.get('doi', '').strip() or None,
            'title': e.get('title', '').strip() or None,
            'year': e.get('year', '').strip() or None,
            'author': e.get('author', '').strip() or None,
        }
    return entries


def extract_md_keys(md_path):
    """提取 MD 中所有 [@citekey]，按出现顺序去重"""
    with open(md_path, encoding='utf-8') as f:
        text = f.read()
    keys = re.findall(r'\[@([\w-]+)', text)
    seen = {}
    for k in keys:
        if k not in seen:
            seen[k] = len(seen) + 1
    return seen  # {cite_key: order_number}


def cross_validate(md_path, bib_path):
    """MD ↔ BIB 交叉验证"""
    md_keys = extract_md_keys(md_path)
    bib_entries = load_bib(bib_path)
    bib_keys = set(bib_entries.keys())

    missing_in_bib = sorted(set(md_keys.keys()) - bib_keys)
    unused_in_md = sorted(bib_keys - set(md_keys.keys()))
    matched = set(md_keys.keys()) & bib_keys

    no_doi = sorted(k for k in matched if not bib_entries[k].get('doi'))
    no_title = sorted(k for k in matched if not bib_entries[k].get('title'))

    result = {
        'md_total': len(md_keys),
        'bib_total': len(bib_entries),
        'matched': len(matched),
        'missing_in_bib': missing_in_bib,
        'unused_in_md': unused_in_md,
        'no_doi': no_doi,
        'no_title': no_title,
    }
    return result


def find_s2_script():
    """自动查找 s2_api.py"""
    candidates = [
        os.path.expanduser('~/.pi/agent/skills/literature_search_semanticscholar/scripts/s2_api.py'),
        os.path.expanduser('~/.claude/skills/literature_search_semanticscholar/scripts/s2_api.py'),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def query_s2(s2_script, doi=None, title=None):
    """查询 Semantic Scholar"""
    try:
        if doi:
            r = subprocess.run(
                ['uv', 'run', s2_script, 'paper', '--id', f'DOI:{doi}',
                 '--fields', 'title,year,authors'],
                capture_output=True, text=True, timeout=20
            )
        elif title:
            r = subprocess.run(
                ['uv', 'run', s2_script, 'title-match', '--query', title,
                 '--fields', 'title,year,authors'],
                capture_output=True, text=True, timeout=20
            )
        else:
            return None

        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            if isinstance(data, dict):
                if 'data' in data and data['data']:
                    return data['data'][0]
                if data.get('paperId'):
                    return data
        return None
    except Exception:
        return None


def query_crossref(doi=None, title=None):
    """查询 CrossRef"""
    try:
        if doi:
            r = subprocess.run(
                ['curl', '-s', f'https://api.crossref.org/works/{doi}'],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                msg = data.get('message', {})
                date_parts = msg.get('published-print', msg.get('published-online', {})).get('date-parts', [[None]])[0]
                return {
                    'title': msg.get('title', [''])[0],
                    'year': str(date_parts[0]) if date_parts[0] else None,
                    'authors': [a.get('family', '') for a in msg.get('author', [])],
                }
        elif title:
            import urllib.parse
            q = urllib.parse.quote(title)
            r = subprocess.run(
                ['curl', '-s', f'https://api.crossref.org/works?query.title={q}&rows=1'],
                capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout)
                items = data.get('message', {}).get('items', [])
                if items:
                    item = items[0]
                    date_parts = item.get('published-print', item.get('published-online', {})).get('date-parts', [[None]])[0]
                    return {
                        'title': item.get('title', [''])[0],
                        'year': str(date_parts[0]) if date_parts[0] else None,
                        'authors': [a.get('family', '') for a in item.get('author', [])],
                    }
        return None
    except Exception:
        return None


def normalize(text):
    """去标点+小写，用于标题比较"""
    return re.sub(r'[^\w]', '', text).lower() if text else ''


def dual_verify(bib_entries, s2_script=None):
    """双来源文献真实性核查 (S2 + CrossRef)"""
    results = {}
    total = len(bib_entries)

    for i, (cite_key, entry) in enumerate(bib_entries.items()):
        doi = entry.get('doi')
        title = entry.get('title')

        # 来源1: Semantic Scholar
        s2 = query_s2(s2_script, doi=doi, title=title) if s2_script else None

        # 来源2: CrossRef
        cr = query_crossref(doi=doi, title=title)

        # 限速
        time.sleep(1)

        s2_ok = s2 is not None
        cr_ok = cr is not None
        issues = []

        # 核对信息一致性
        if s2_ok:
            if normalize(s2.get('title', '')) != normalize(title or ''):
                issues.append(f"S2 title mismatch")
            s2_year = str(s2.get('year', ''))
            if entry.get('year') and s2_year and s2_year != 'None' and s2_year != entry['year']:
                issues.append(f"S2 year={s2_year} vs BIB={entry['year']}")
        if cr_ok:
            if normalize(cr.get('title', '')) != normalize(title or ''):
                issues.append(f"CR title mismatch")
            cr_year = cr.get('year')
            if entry.get('year') and cr_year and cr_year != 'None' and cr_year != entry['year']:
                issues.append(f"CR year={cr_year} vs BIB={entry['year']}")

        # 判定
        if not s2_ok and not cr_ok:
            status = 'SKIP'
        elif not s2_ok or not cr_ok:
            missing = 'S2' if not s2_ok else 'CR'
            status = 'FAIL'
            issues.append(f"{missing} 未找到")
        elif issues:
            status = 'WARN'
        else:
            status = 'PASS'

        results[cite_key] = {
            'status': status,
            'issues': issues,
            's2_found': s2_ok,
            'cr_found': cr_ok,
        }

        # 进度
        print(f'  [{i+1}/{total}] {cite_key}: {status}', flush=True)

    return results


def print_cross_report(result):
    """打印交叉验证报告"""
    print('\n交叉验证报告')
    print('━' * 50)
    print(f'  MD 引用数: {result["md_total"]}    BIB 条目数: {result["bib_total"]}')
    print(f'  ✅ 匹配: {result["matched"]}')

    if result['missing_in_bib']:
        print(f'\n❌ MD 引用了但 BIB 缺少 ({len(result["missing_in_bib"])}):  ← 必须修复')
        for k in result['missing_in_bib']:
            print(f'   - {k}')

    if result['unused_in_md']:
        print(f'\n⚠️  BIB 有但 MD 未引用 ({len(result["unused_in_md"])}):  ← 可选清理')
        for k in result['unused_in_md'][:10]:
            print(f'   - {k}')
        if len(result['unused_in_md']) > 10:
            print(f'   ... 等 {len(result["unused_in_md"])} 条')

    if result['no_doi']:
        print(f'\nℹ️  无 DOI 走标题匹配 ({len(result["no_doi"])}/{result["matched"]}):  ← 提醒')

    if result['no_title']:
        print(f'\n❌ 无 title 无法匹配 ({len(result["no_title"])}):  ← 必须修复')
        for k in result['no_title']:
            print(f'   - {k}')

    print('━' * 50)


def print_verify_report(results):
    """打印双来源核查报告"""
    pass_count = sum(1 for r in results.values() if r['status'] == 'PASS')
    warn_count = sum(1 for r in results.values() if r['status'] == 'WARN')
    fail_count = sum(1 for r in results.values() if r['status'] == 'FAIL')
    skip_count = sum(1 for r in results.values() if r['status'] == 'SKIP')

    print(f'\n双来源文献真实性核查报告')
    print('━' * 50)
    print(f'  核查条目: {len(results)}    ✅ PASS: {pass_count}    '
          f'⚠️ WARN: {warn_count}    ❌ FAIL: {fail_count}    ⏭ SKIP: {skip_count}')

    fails = [(k, v) for k, v in results.items() if v['status'] == 'FAIL']
    if fails:
        print(f'\n❌ FAIL ({len(fails)}):  ← 必须修正')
        for k, v in fails:
            print(f'   - {k}: {"; ".join(v["issues"])}')

    warns = [(k, v) for k, v in results.items() if v['status'] == 'WARN']
    if warns:
        print(f'\n⚠️  WARN ({len(warns)}):  ← 需人工确认')
        for k, v in warns:
            print(f'   - {k}: {"; ".join(v["issues"])}')

    skips = [(k, v) for k, v in results.items() if v['status'] == 'SKIP']
    if skips:
        print(f'\n⏭  SKIP ({len(skips)}):  ← 无法验证')
        for k, v in skips:
            print(f'   - {k}')

    print('━' * 50)


def main():
    parser = argparse.ArgumentParser(description='MD↔BIB 交叉验证 + 双来源文献核查')
    parser.add_argument('md_path', help='Markdown 文件路径')
    parser.add_argument('bib_path', help='BibTeX 文件路径')
    parser.add_argument('--verify', action='store_true', help='启用双来源文献真实性核查')
    parser.add_argument('--s2-script', help='s2_api.py 路径')
    parser.add_argument('--json', help='输出结果到 JSON 文件')
    args = parser.parse_args()

    # Step 2b: 交叉验证
    print('Step 2b: MD ↔ BIB 交叉验证')
    cv_result = cross_validate(args.md_path, args.bib_path)
    print_cross_report(cv_result)

    fatal = cv_result['missing_in_bib'] + cv_result['no_title']
    if fatal:
        print(f'\n⛔ 存在致命问题，请修复后重新运行。')
        if args.json:
            with open(args.json, 'w') as f:
                json.dump({'cross_validate': cv_result}, f, ensure_ascii=False, indent=2)
        return 1

    # Step 2c: 双来源核查（可选）
    if args.verify:
        print('\nStep 2c: 双来源文献真实性核查 (S2 + CrossRef)')
        s2_script = args.s2_script or find_s2_script()
        if not s2_script:
            print('⚠️  未找到 s2_api.py，跳过 S2 核查，仅使用 CrossRef')
        
        bib_entries = load_bib(args.bib_path)
        # 只核查 MD 中实际引用的条目
        md_keys = extract_md_keys(args.md_path)
        verify_entries = {k: v for k, v in bib_entries.items() if k in md_keys}
        
        v_results = dual_verify(verify_entries, s2_script)
        print_verify_report(v_results)
    else:
        v_results = None

    # 输出 JSON
    if args.json:
        output = {'cross_validate': cv_result}
        if v_results:
            output['dual_verify'] = v_results
        with open(args.json, 'w') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f'\n结果已保存到 {args.json}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
