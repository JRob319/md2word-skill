---
name: md2word-skill
description: "从 Markdown + BibTeX 生成 Zotero 管理的 Word 文档。将 pandoc 引用 [@key] 转为 Zotero CSL_CITATION field codes。触发词: /md2word, md转word, markdown转word, zotero field codes, 参考文献管理, 论文格式化, BibTeX to Word, pandoc to Word, Zotero citation injection."
---

# /md2word: MD + BIB → Zotero-managed Word

## 前提条件

- **Markdown 文件**：含 pandoc 风格引用（`[@key]` 或 `[@key1; @key2]`）
- **BibTeX 文件**：包含所有引用的参考文献（每条最好有 DOI）
- **Zotero 桌面版**：正在运行（pyzotero 需要本地 API）
- **依赖**：pandoc 2.11+（`--citeproc`）、pyzotero、python-docx、lxml、bibtexparser

## 工作流程

```
Step 1  收集参数 (md_file, bib_file, collection, csl_style)
Step 2  依赖检查 + 交叉验证 + 双来源文献核查
    ↓ 仅 PASS 条目继续
Step 3  创建 Zotero Collection + 导入已验证文献
Step 4  构建 cite_key → Zotero item key 映射
Step 5  pandoc MD → Word (指定 CSL 样式)
Step 6  注入 Zotero field codes (根据 citation-format 适配)
```

---

### Step 1: 收集参数

向用户确认（未提供则用默认值）：

| 参数 | 必需 | 默认值 |
|------|------|--------|
| md_file | ✅ | — |
| bib_file | ✅ | — |
| collection | — | BIB 文件名去掉扩展名 |
| output | — | `<md文件名>_zotero.docx` |
| user_id | — | 3313474 |
| csl_style | — | `physics-in-medicine-and-biology` |

**CSL 样式解析**（`resolve_csl()`）：
1. 完整路径 → 直接使用
2. 样式名 → 查 `styles/` 目录下 `.csl` 文件
3. dependent 样式（含 `independent-parent` 链接）→ 自动使用父样式
4. 找不到 → 报错并给出 `curl` 下载命令

已安装样式见 `styles/` 目录，添加新样式：
```bash
curl -sL https://raw.githubusercontent.com/citation-style-language/styles/master/<name>.csl \
  -o ~/.claude/skills/md2word-skill/styles/<name>.csl
```

---

### Step 2: 依赖检查 + 验证

#### 2a. 检查依赖

```bash
python3 -c "from pyzotero import zotero; zot = zotero.Zotero(0, 'user', local=True); print(len(zot.collections()), 'collections')"
pandoc --version | head -1
```

Zotero 未运行则提示启动后重试。

#### 2b + 2c. 交叉验证 + 双来源文献真实性核查

统一调用 `scripts/verify_references.py`：

```bash
# 仅交叉验证（快速）
python3 scripts/verify_references.py MD_FILE BIB_FILE

# 交叉验证 + 双来源核查（完整）
python3 scripts/verify_references.py MD_FILE BIB_FILE --verify

# 输出 JSON 供后续步骤使用
python3 scripts/verify_references.py MD_FILE BIB_FILE --verify --json /tmp/verify_result.json
```

**脚本流程**（`verify_references.py`）：
1. `bibtexparser` 解析 BIB + 正则提取 MD 引用 `[@key]`
2. **交叉验证**：`cross_validate()` — missing_in_bib（致命）、unused_in_md（警告）、no_doi/no_title
3. **双来源核查**：`dual_verify()` — 对 MD 引用的条目查询 S2 + CrossRef
   - 有 DOI → S2 `paper --id DOI:xxx` + CrossRef `GET /works/{DOI}`
   - 无 DOI → S2 `title-match` + CrossRef `query.title={title}`
4. 对比 title（去标点）、year、第一作者姓氏

**判定规则**：
- ✅ **PASS**：S2 + CR 都找到且信息一致
- ⚠️ **WARN**：都找到但不一致（暂停，展示差异）
- ❌ **FAIL**：至少一源未找到（暂停，必须修正）
- ⏭ **SKIP**：两源均未找到（标记未验证）

**检查点**：脚本输出报告后，致命问题需修复才继续。

> S2 需要 `SEMANTIC_SCHOLAR_API_KEY`，CrossRef 免费。找不到 `s2_api.py` 时降级为 CrossRef 单源并提示。
---

### Step 3: 创建 Zotero Collection + 导入已验证文献

仅导入 **PASS** 状态的条目。

**3a. 新建/复用 Collection**：
用 `pyzotero` 检查是否已存在同名 collection，不存在则创建。

**3b. 批量导入**：
- 有 DOI 的条目：创建 `journalArticle` 模板，只填 DOI，Zotero 自动补全元数据
- 无 DOI 的条目：填入 title、year、authors（从 BIB 解析）

**3c. 等待同步**：`sleep 10`，确认 collection 中条目数。

**检查点**：展示导入报告（已导入数 / 跳过数及原因），等用户确认。

> FAIL/SKIP 不导入——真实性存疑的文献不应污染 Zotero 库。WARN 条目用户可手动导入。

---

### Step 4: 构建 cite_key → Zotero item key 映射

从 Step 3 的 collection 中查找（不是全库），用 BIB 的 DOI/title 做桥梁：

```
cite_key → (doi, title) from BIB → match against collection items → Zotero item key
```

匹配优先级：DOI 精确匹配 > 标题去标点后精确匹配。

**检查点**：未匹配列表 > 0 时暂停，等用户决定（手动指定 / 忽略 / 中止）。

保存映射到 `/tmp/citation_mapping.json`，格式：`{"1": "J8K6WXE8", ...}`

---

### Step 5: pandoc MD → Word

```bash
pandoc INPUT.md --citeproc --bibliography=REFERENCES.bib --csl=CSL_PATH -o /tmp/pandoc_output.docx
```

`CSL_PATH` 由 Step 1 的 `resolve_csl()` 返回。

pandoc 后自动检测 CSL 的 `citation-format`（解析 XML 中 `<category citation-format="...">`）：
- `author-date` → Step 6 用 Author+Year 匹配
- `numeric` → Step 6 用编号匹配
- `note` → Step 6 用脚注标记匹配

**检查点**：pandoc 报错或输出为空时暂停。

---

### Step 6: 注入 Zotero field codes

根据 Step 5 检测的 citation-format 选注入策略：

**author-date 模式**（PMB / IOP Harvard）：
- pandoc 输出 `(Harman et al 2008)` 格式
- 用 BIB 的 authors+year 反查 cite_key
- 用 mapping 替换为 `ADDIN CSL_CITATION` field code

**numeric 模式**：
- pandoc 输出 `[1]` 或上标
- 用编号直接匹配 mapping

执行注入：
```bash
python3 scripts/inject_zotero.py --input /tmp/pandoc_output.docx --output FINAL.docx \
  --mapping /tmp/citation_mapping.json --user-id USER_ID --format <citation-format>
```

脚本同时：删除静态 References 节，插入 `ADDIN ZOTERO_BIBLIOGRAPH` 占位符。

**验证**：
```bash
python3 -c "import zipfile,re; z=zipfile.ZipFile('OUTPUT.docx'); c=z.open('word/document.xml').read().decode();
  print(f'✓ {len(re.findall(r\"ZOTERO_ITEM CSL_CITATION\", c))} citations');
  print(f'✓ {len(re.findall(r\"ZOTERO_BIBLIOGRAPH\", c))} bibliography')"
```

**检查点**：展示替换统计，确认数量合理。

---

## 注意事项

- **不要覆盖**现有文件，输出写到新路径
- **不要修改**现有 Zotero collection，除非用户明确要求
- pandoc 需 2.11+（支持 `--citeproc`）
- 已有 Word 文件只需注入 → 跳过 Step 5，直接 Step 4+6
- CSL 是工作流核心——决定引用格式、参考文献列表格式、注入策略

## 边界条件

| 情况 | 处理 |
|------|------|
| BIB 非 UTF-8 | `iconv -f GBK -t UTF-8` 转码 |
| MD 无引用标记 | 跳过 Step 4-6，仅 pandoc 转换 |
| Collection 为空 | 提示先导入，暂停 |
| cite_key 重复 | 取最后一条，报告中标注 |
| 条目无 DOI 无 title | 无法匹配，报告让用户指定 |
| CSL 是 dependent | 自动找父样式，找不到则报错 |
| CSL 不存在 | 列出已有样式，提示下载 |
| 映射不完整 | 跳过未映射引用，输出警告 |
