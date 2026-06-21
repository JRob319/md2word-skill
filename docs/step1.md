# Step 1: 收集参数 & 确认

**必须逐项确认，不能跳过。** 分三个阶段：

## 1a. 收集 & 展示

从用户指令中提取参数，未提供的填默认值，然后**打印参数汇总表**：

输入文件支持 `.md`（pandoc `[@key]` 引用）和 `.tex`（`\cite{}`/`\citep{}`/`\citet{}` 等）。

```text
═══════════════════════════════════════
  md2word 参数确认
═══════════════════════════════════════
  输入文件:       /path/to/paper.md  （或 paper.tex）
  BIB 文件:       /path/to/references.bib
  CSL 样式:       physics-in-medicine-and-biology
                   → citation-format: author-date
  Zotero 用户 ID: 0 (本地)
  Collection:     references
  输出文件:       /path/to/paper_zotero.docx
  ─────────────────────────────────────
  OUTDIR:         /path/to/
═══════════════════════════════════════
```

必填项缺失时**必须暂停询问**，不能用默认值代替：

| 参数 | 必需 | 默认值 | 缺失时 |
|------|------|--------|--------|
| input_file | ✅ | — | **暂停，询问文件路径**（`.md` 或 `.tex`） |
| bib_file | ✅ | — | **暂停，询问文件路径** |
| collection | — | BIB 文件名去掉扩展名 | 静默使用默认 |
| output | — | `OUTDIR/<md文件名>_zotero.docx` | 静默使用默认 |
| user_id | — | `0`（本地 Zotero） | 静默使用默认 |
| csl_style | ✅ | `physics-in-medicine-and-biology` | 用户未指定则使用默认 |

> **CSL 文件路径**：默认 CSL 文件在本 skill 的 `styles/physics-in-medicine-and-biology.csl`。它是 dependent style，parent 是同目录的 `institute-of-physics-harvard.csl`，pandoc 会自动解析。路径相对于本 skill 目录。

## 1b. 环境预检

**文件检查** — 确认存在且格式正确：

```bash
# 检查文件存在
ls -la INPUT_FILE BIB_FILE
# 统计引用数（MD 用 [@，LaTeX 用 \cite）
grep -c '\[@'  INPUT_FILE  2>/dev/null || grep -c '\\cite' INPUT_FILE
# 统计 BIB 条目数
grep -c '^@' BIB_FILE
# 检查 CSL 并显示 citation-format
python3 -c "import xml.etree.ElementTree as ET; ..."
```

**Zotero 连通性检查** — 必须在 Step 2 之前完成：

```bash
# 1) Zotero 桌面是否运行（本地 API，只读）
curl -s http://localhost:23119/api/users/0/collections | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'本地API: ✅ {len(d)} collections')" \
  || echo '本地API: ❌ Zotero 未运行'

# 2) Zotero Web API 是否可用（读写，Step 3 必需）
curl -s -H "Zotero-API-Key: $ZOTERO_API_KEY" "https://api.zotero.org/users/$ZOTERO_USER_ID/collections?limit=1" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Web API: ✅ user={d[0][\"library\"][\"name\"]}')" \
  || echo 'Web API: ❌ 未配置 ZOTERO_API_KEY / ZOTERO_USER_ID'
```

打印摘要：
```text
  MD: 118 行, 50 个唯一引用
  BIB: 58 个条目 (26% DOI 覆盖率)
  CSL: author-date (IOP Harvard)
  Zotero 本地API: ✅ (读取)
  Zotero Web API: ✅ (读写, Step 3-4 必需)
```

- 缺少 `ZOTERO_API_KEY` 或 `ZOTERO_USER_ID` 时 → **提示用户手动操作**：
  1. 与用户确认 Collection 名称（默认取 BIB 文件名去掉扩展名）
  2. 用户在 Zotero 中创建该 Collection
  3. 通过 Zotero「导入」功能将 BIB 文件导入该 Collection
  4. 等待用户确认「已完成导入」后才继续 Step 2

## 1c. 汇总确认

展示以上全部信息。**必填项（md_file、bib_file）无缺项时，自动进入 Step 2**，无需等用户回复。仅有必填项缺失时才暂停询问。
