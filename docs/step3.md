# Step 3: 创建 Zotero Collection + 导入权威元数据

用 `scripts/import_zotero.py` 把 Step 2c 三源裁决后的**权威元数据**（而非 BIB 原值）导入 Zotero。

## 前置
- Step 2c 已产出 `verify_result.json`（含每条的 `authoritative` 元数据）
- `ZOTERO_API_KEY` + `ZOTERO_USER_ID` 已配 —— **local API 不支持写**（实测），导入/写入必须走 Web API

## 3a. 导入

```bash
python3 scripts/import_zotero.py \
  --verify-json OUTDIR/verify_result.json \
  --collection COLLECTION_NAME \
  --bib BIB_FILE
```

可选：`--dry-run`（只报告不写入）、`--import-skip`（SKIP 条目也用 BIB 原值导入并标记「未验证」）。

**脚本逻辑**：
- **PASS** → 用权威元数据 `create_items` 新建 item
- **FLAG** → 用权威元数据新建 + 冲突记录写入 `Extra` 字段（`--strict` 时这些已在 Step 2c 阻塞）
- **REJECT / SKIP** → 不导入（真实性存疑，不污染库）；`--import-skip` 可强制用 BIB 原值导入
- collection 内已有**同 DOI** item → `update_item` 修正（含作者名）；否则 `create_items`

## 3b. 核心价值：修正 BIB 错误

即使 BIB 里作者名写错（如 `Fogliato`），三源裁决给的是权威值（`Fogliata`），导入即为正确值。这是本步骤相对「直接导入 BIB」的根本优势 —— **BIB 的错误被权威数据覆盖**。同理修正年份、期刊、卷期页。

> ⚠️ 不要用「只填 DOI，Zotero 自动补全」的旧思路：pyzotero（无论 local 还是 Web API）创建只含 DOI 的 item **不会触发** Zotero 客户端的自动补全（实测得到的是残缺条目）。必须用 verify 的权威数据构造完整 payload。

## 3c. 等待同步 & 校验

`sleep 5`，确认 collection 中条目数。**再次运行** `import_zotero.py` 应全部为 `↻ 更新`（同 DOI 不重复创建）—— 这是去重正确性的验证。

**检查点**：展示导入报告（新建 / 更新 / FLAG / 跳过），等用户确认。

> REJECT/SKIP 不导入——真实性存疑的文献不应污染 Zotero 库。FLAG 已导入（用最优值），但 Extra 字段有冲突记录，投稿前应逐条过目。
