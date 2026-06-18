# Step 2: 依赖检查 + 验证

## 2a. 检查依赖

```bash
# 读取用 local API；写入（Step 3）必须用 Web API
python3 -c "from pyzotero import zotero; zot = zotero.Zotero(0, 'user', local=True); print(len(zot.collections()), 'collections')"
pandoc --version | head -1
```

Zotero 未运行则提示启动后重试。

## 2b + 2c. 交叉验证 + 三源核查与元数据裁决

统一调用 `scripts/verify_references.py`：

```bash
# 仅交叉验证（快速，默认）
python3 scripts/verify_references.py MD_FILE BIB_FILE

# 交叉验证 + 三源核查（完整路径）
python3 scripts/verify_references.py MD_FILE BIB_FILE --verify

# FLAG 也阻塞（投稿前终审）
python3 scripts/verify_references.py MD_FILE BIB_FILE --verify --strict

# 输出 JSON（含权威元数据）供 Step 3 导入
python3 scripts/verify_references.py MD_FILE BIB_FILE --verify --json OUTDIR/verify_result.json
```

**三源**（均免费，无需 key）：
- **CrossRef** — DOI 锚点 / 期刊 / 卷期页 / 年份（出版商直供）
- **PubMed**（NCBI E-utilities）— 生物医学金标准 / 作者全名最规范（NLM 独立策展）
- **OpenAlex** — 覆盖最广 / 作者机构 / ORCID

**裁决流程**：归一化消假冲突 → 判定同篇 → 三档处置
- ✅ **PASS**：三源一致（或归一化后一致）
- ⚠️ **FLAG**：实质冲突但有合理默认 → 默认**不阻塞**（导入但标记到 Extra）；`--strict` 时阻塞
- ❌ **REJECT**：不像同一篇文献（title/作者/年份不匹配）→ 不导入
- ⏭ **SKIP**：三源均未找到 → 不导入

**字段最优源**（真冲突时取谁）：作者优先 **PubMed**（独立策展，单票分量 ≥ CrossRef+OpenAlex）；期刊/卷期页/年份优先 **CrossRef**。

> ⚠️ 源并不独立：OpenAlex 大量数据继承自 CrossRef，故不简单数人头，PubMed 的独立一票分量更高。

**检查点**：脚本输出报告（PASS/FLAG/REJECT/SKIP 计数 + FLAG 的逐字段冲突详情）。`--strict` 下 FLAG/REJECT 阻塞。

> `verify_result.json` 含每条的 `authoritative` 权威元数据，供 Step 3 的 `import_zotero.py` 使用——这是「不信任 bib、用权威数据」修正错误的关键。
