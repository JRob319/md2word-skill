#!/usr/bin/env python3
"""
import_zotero.py — 用三源裁决后的权威元数据导入 Zotero collection

承接 verify_references.py --json 的输出。对每个 PASS/FLAG 条目, 用【权威元数据】
(而非 bib 原值) 构造完整 Zotero item —— 这正是修正 bib 错误(如作者名)的核心:
即使 bib 把作者写成 Fogliato, 三源裁决给的是 Fogliata, 导入即为正确值。

FLAG 条目额外把冲突记录写入 Extra 字段。collection 内已有同 DOI item → update_item
修正(A2 实测可改作者名); 否则 create_items。

前置: python3 verify_references.py MD BIB --verify --json OUT.json
写入: local API 不支持写(A0 实测), 故必须用 Web API (ZOTERO_API_KEY)

用法:
  python3 import_zotero.py \
      --verify-json verify_out.json --collection "Acuros XB" \
      [--bib refs.bib] [--user-id ID] [--api-key KEY] [--dry-run] [--import-skip]

  --dry-run      只报告不写入
  --import-skip  SKIP 条目也用 bib 数据导入(标记"三源未验证")
"""
import argparse
import json
import os
import sys
import time


def connect(user_id, api_key):
    from pyzotero import zotero
    return zotero.Zotero(user_id, "user", api_key)


def _retry(fn, label, tries=4):
    """Zotero Web API 写操作重试（网络抖动 / 502 / SSL 超时常见）。失败返回 None，不中断整体。"""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if i == tries - 1:
                print(f"  ✗ {label} 网络失败({type(e).__name__})，跳过该条")
                return None
            print(f"    ⚠ {label} 网络错误({type(e).__name__})，{2*(i+1)}s 后重试...")
            time.sleep(2 * (i + 1))


def auth_to_item(auth, bib_entry):
    """裁决后权威元数据 → Zotero item payload"""
    creators = [{"creatorType": "author",
                 "firstName": (a.get("given") or ""),
                 "lastName": (a.get("family") or "")}
                for a in auth.get("authors", [])]
    doi = auth.get("doi") or (bib_entry or {}).get("doi") or ""
    return {
        "itemType": "journalArticle",
        "title": auth.get("title", "") or "",
        "creators": creators,
        "date": auth.get("year", "") or "",
        "publicationTitle": auth.get("journal", "") or "",
        "volume": auth.get("volume", "") or "",
        "issue": auth.get("issue", "") or "",
        "pages": auth.get("pages", "") or "",
        "DOI": doi,
    }


def bib_authors_to_creators(author_str):
    """bib author 字符串 → creators (--import-skip 的 SKIP fallback 用)。
    规则(docs/step3.md): 'and others' 丢弃; 'Last, First' 拆分; 'First Last' 末词为姓"""
    creators = []
    for raw in str(author_str or "").split(" and "):
        raw = raw.strip().strip("{}")
        if not raw or raw.lower() == "others":
            continue
        if "," in raw:
            last, first = [p.strip() for p in raw.split(",", 1)]
        else:
            parts = raw.split()
            if len(parts) >= 2:
                first, last = " ".join(parts[:-1]), parts[-1]
            else:
                first, last = "", raw
        creators.append({"creatorType": "author", "firstName": first, "lastName": last})
    return creators


def extra_from_conflicts(conflicts):
    if not conflicts:
        return ""
    lines = ["⚠️ METADATA CONFLICT (md2word 三源裁决):"]
    for c in conflicts:
        vals = " | ".join(f"{s}={v}" for s, v in c["values"].items())
        lines.append(f'  {c["field"]}: {vals}  → 取 {c["chosen_source"]}')
    return "\n".join(lines)


def find_existing_by_doi(zot, coll_key, doi):
    if not doi or not coll_key:
        return None
    for it in zot.collection_items(coll_key):  # 默认前 100 条；大 collection 需分页
        d = it.get("data", {})
        if d.get("itemType") in ("attachment", "note"):
            continue
        if (d.get("DOI") or "").lower() == doi.lower():
            return it
    return None


def main():
    ap = argparse.ArgumentParser(description="用权威元数据导入 Zotero (承接 verify_references --json)")
    ap.add_argument("--verify-json", required=True, help="verify_references.py --json 输出")
    ap.add_argument("--collection", required=True, help="Zotero collection 名")
    ap.add_argument("--bib", help="BibTeX (--import-skip fallback 用)")
    ap.add_argument("--user-id", default=os.environ.get("ZOTERO_USER_ID"))
    ap.add_argument("--api-key", default=os.environ.get("ZOTERO_API_KEY"))
    ap.add_argument("--dry-run", action="store_true", help="只报告不写入")
    ap.add_argument("--import-skip", action="store_true", help="SKIP 条目也用 bib 数据导入(标记未验证)")
    ap.add_argument("--output-mapping", help="输出 cite_key→Zotero key 映射(含置信度/审计)，默认 verify.json 同目录 mapping.json")
    args = ap.parse_args()

    if not (args.user_id and args.api_key):
        sys.exit("❌ 需 ZOTERO_USER_ID + ZOTERO_API_KEY (或 --user-id/--api-key)")

    zot = connect(args.user_id, args.api_key)
    coll_key = next((c["data"]["key"] for c in zot.collections()
                     if c["data"]["name"] == args.collection), None)
    if coll_key:
        print(f"collection: {args.collection} (已存在 key={coll_key})")
    elif args.dry_run:
        print(f"collection: {args.collection} (不存在; dry-run 将创建)")
    else:
        resp = zot.create_collections([{"name": args.collection}])
        coll_key = list(resp.get("success", {}).values())[0]
        print(f"collection: {args.collection} (新建 key={coll_key})")

    bib = {}
    if args.bib:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import verify_references
        bib = verify_references.load_bib(args.bib)

    verify = json.load(open(args.verify_json))
    mv = verify.get("multi_verify", {})

    stats = {"created": 0, "updated": 0, "skipped": 0, "flagged": 0}
    mapping_out = {}  # cite_key → {zotero_key, anchor, confidence, status}（带置信度与审计）
    for cite_key, v in mv.items():
        status, auth, be = v["status"], v.get("authoritative", {}), bib.get(cite_key, {})

        if status in ("PASS", "FLAG"):
            item_data = auth_to_item(auth, be)
            extra = extra_from_conflicts(v.get("conflicts", []))
            if extra:
                item_data["extra"] = extra
                stats["flagged"] += 1
            tag = "FLAG " if status == "FLAG" else "     "
            # 置信度：DOI 强锚点 high / title 反查 medium
            has_doi = bool(item_data.get("DOI"))
            anchor, confidence = ("doi", "high") if has_doi else ("title", "medium")
        elif status == "SKIP" and args.import_skip:
            item_data = {
                "itemType": "journalArticle",
                "title": be.get("title", "") or "",
                "creators": bib_authors_to_creators(be.get("author")),
                "date": be.get("year", "") or "",
                "DOI": be.get("doi", "") or "",
                "extra": "⚠️ 三源未验证 (SKIP), 使用 bib 原值",
            }
            tag = "SKIP "
            anchor, confidence = "bib", "low"  # 未三源验证
        else:
            stats["skipped"] += 1
            print(f"  ⊘ {cite_key}: {status} 不导入")
            continue

        doi = item_data.get("DOI", "")
        existing = find_existing_by_doi(zot, coll_key, doi)
        lead = item_data["creators"][0]["lastName"] if item_data["creators"] else "?"
        zotero_key = ""

        if existing:
            for k, val in item_data.items():
                if k == "itemType":
                    continue
                if k == "extra" and existing["data"].get("extra"):
                    val = existing["data"]["extra"] + "\n" + val
                existing["data"][k] = val
            if not args.dry_run:
                if _retry(lambda: zot.update_item(existing), cite_key) is None:
                    continue
                time.sleep(0.5)
            zotero_key = existing["data"].get("key", "")
            stats["updated"] += 1
            print(f'  ↻ {tag}{cite_key}: 更新 → {lead} et al. ({item_data["date"]})')
        else:
            item_data["collections"] = [coll_key]
            if not args.dry_run:
                resp = _retry(lambda: zot.create_items([item_data]), cite_key)
                if resp is None:
                    continue
                if resp.get("failed"):
                    print(f"  ✗ {cite_key} 创建失败: {resp['failed']}")
                    continue
                zotero_key = list(resp.get("success", {}).values())[0] if resp.get("success") else ""
                time.sleep(0.5)
            stats["created"] += 1
            print(f'  + {tag}{cite_key}: 新建 → {lead} et al. ({item_data["date"]})')

        mapping_out[cite_key] = {"zotero_key": zotero_key, "anchor": anchor,
                                 "confidence": confidence, "status": status}

    # 输出 mapping（带置信度/审计）—— 消除单独 Step4 读 collection 的网络依赖
    out_mapping = args.output_mapping or os.path.join(
        os.path.dirname(os.path.abspath(args.verify_json)), "mapping.json")
    json.dump(mapping_out, open(out_mapping, "w"), ensure_ascii=False, indent=2)
    low_conf = [k for k, v in mapping_out.items() if v["confidence"] != "high"]
    suffix = " (DRY-RUN, 未写入)" if args.dry_run else ""
    print(f"\n完成: 新建 {stats['created']} | 更新 {stats['updated']} | FLAG {stats['flagged']} | 跳过 {stats['skipped']}{suffix}")
    print(f"映射 {len(mapping_out)} 条 → {out_mapping}")
    if low_conf:
        print(f"⚠️ 低置信 {len(low_conf)} 条（非 DOI 锚点，建议人工确认）:")
        for k in low_conf:
            v = mapping_out[k]
            print(f"   - {k}: anchor={v['anchor']} confidence={v['confidence']} ({v['status']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
