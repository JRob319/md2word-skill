---
name: md2word-skill
description: "从 Markdown + BibTeX 生成 Zotero 管理的 Word 文档。将 pandoc 引用 [@key] 转为 Zotero CSL_CITATION field codes。触发词: /md2word, md转word, markdown转word, zotero field codes, 参考文献管理, 论文格式化, BibTeX to Word, pandoc to Word, Zotero citation injection."
---

# /md2word: MD + BIB → Zotero-managed Word

## 前提条件

用户必须具备：
- **Markdown 文件**：含 pandoc 风格引用（`[@citekey]` 或 `[@key1; @key2]`）
- **BibTeX 文件**：包含所有引用的参考文献（每条最好有 DOI）
- **Zotero 桌面版**：正在运行（pyzotero 需要本地 API）
- **依赖**：pandoc 2.11+（支持 `--citeproc`）、pyzotero、python-docx、lxml

## 工作流程（6 步）

### Step 1: 收集参数

向用户确认以下信息（如果未提供）：

```
必需：
- md_file:    Markdown 文件路径
- bib_file:   BibTeX 文件路径

可选（有默认值）：
- collection: Zotero collection 名称（默认：BIB 文件名去掉扩展名）
- output:     输出 Word 文件路径（默认：<md文件名>_zotero.docx）
- user_id:    Zotero user ID（默认：3313474）
```

### Step 2: 依赖检查 + 交叉验证

**2a. 检查依赖**：
```bash
python3 -c "from pyzotero import zotero; zot = zotero.Zotero(0, 'user', local=True); print(len(zot.collections()), 'collections')" 2>&1
pandoc --version | head -1
```
Zotero 未运行则提示启动后重试。

**2b. MD ↔ BIB 交叉验证**（在进入 Step 3 之前执行）：

用 MD 的引用和 BIB 的条目做双向校验，提前发现问题：
```python
import bibtexparser, re

def cross_validate(md_path, bib_path):
    # 提取 MD 中所有 [@citekey]
    with open(md_path, encoding='utf-8') as f:
        md_text = f.read()
    md_keys = set(re.findall(r'\[@([\w-]+)', md_text))

    # 提取 BIB 中所有 entry ID
    with open(bib_path, encoding='utf-8') as f:
        db = bibtexparser.load(f)
    bib_keys = {e['ID']: e for e in db.entries}

    # ① 致命：MD 引用了但 BIB 没有 → pandoc 会渲染失败
    missing_in_bib = md_keys - set(bib_keys.keys())

    # ② 警告：BIB 有但 MD 没引用 → 冗余条目
    unused_in_md = set(bib_keys.keys()) - md_keys

    # ③ 信息：匹配上的条目中，缺 DOI 的 → 只能走标题 fallback
    matched = md_keys & set(bib_keys.keys())
    no_doi = [k for k in matched if not bib_keys[k].get('doi', '').strip()]

    # ④ 信息：缺 title 的 → 无法做任何匹配
    no_title = [k for k in matched if not bib_keys[k].get('title', '').strip()]

    return {
        'md_total': len(md_keys),
        'bib_total': len(bib_keys),
        'missing_in_bib': sorted(missing_in_bib),   # 必须修复
        'unused_in_md': sorted(unused_in_md),       # 可选清理
        'no_doi': sorted(no_doi),                   # 走标题 fallback
        'no_title': sorted(no_title),               # 无法匹配
    }
```

**检查点**：展示交叉验证报告——
```
交叉验证报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MD 引用数: 50    BIB 条目数: 58
  ✅ 匹配: 48

❌ MD 引用了但 BIB 缺少 (2):     ← 必须修复
   - smith2023missing
   - jones2024typo

⚠️  BIB 有但 MD 未引用 (10):      ← 可选清理
   - ronneberger2015unet, ... 等 10 条

ℹ️  无 DOI 走标题匹配 (36):       ← 提醒
   36/48 匹配条目无 DOI，将走标题 fallback

❌ 无 title 无法匹配 (0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

- 如果 `missing_in_bib` 不为空 → **暂停，等用户补全 BIB 或修正 cite key**
- 如果 `no_title` 不为空 → **暂停，等用户补全 title**
- 其余情况正常继续
### Step 3: 导入 BIB → Zotero（如不存在）

先检查目标 collection 是否已存在：
```python
from pyzotero import zotero
zot = zotero.Zotero(0, 'user', local=True)
collections = zot.collections()
target = None
for c in collections:
    if c['data']['name'] == COLLECTION_NAME:
        target = c
        break
```

如果不存在，**提示用户在 Zotero 中手动导入**（File → Import → 选择 BIB 文件 → 移到对应 collection）。pyzotero 的条目创建格式复杂且容易出错，手动导入更可靠。

然后告诉用户：**"请在 Zotero 中导入 BIB 文件并放入 collection，完成后回复'已导入'继续。"**

用户确认后，获取 collection 中所有条目，构建查找表：
```python
items = zot.collection_items(collection_key)
doi_to_key = {}
title_to_key = {}
for item in items:
    d = item['data']
    key = d['key']
    if 'DOI' in d and d['DOI']:
        doi_to_key[d['DOI'].lower().strip()] = key
    title_to_key[d.get('title', '').lower().strip()] = key
```

### Step 4: 构建映射

从 BIB 文件和 MD 文件构建 `pandoc编号 → Zotero item key` 映射。这一步是整个流程的关键——pandoc 按首次出现顺序编号，脚本需要知道每个编号对应哪个 Zotero 条目。

```
2. 匹配逻辑（优先级）：
   - **DOI 精确匹配**：`bib_doi.lower() == zotero_doi.lower()`（最可靠）
   - **标题模糊匹配**：去除标点和空格后比较，`re.sub(r'[^\w]', '', bib_title) == re.sub(r'[^\w]', '', zotero_title)`（fallback）
3. **检查点**：输出未匹配的 cite_key 列表。
   - 如果未匹配数 > 0：**暂停，展示未匹配列表，等用户决定**（手动指定 / 忽略 / 中止）
   - 如果全部匹配：继续
4. 解析 MD 中 `[@citekey]` 的出现顺序，确定 cite_key → pandoc_number
5. 合并：`pandoc_number → Zotero item key`

保存映射到临时 JSON 文件供脚本使用。格式：`{"1": "J8K6WXE8", "2": "7SU3QAU9", ...}`

### Step 5: pandoc MD → Word

```bash
pandoc INPUT.md \
  --citeproc \
  --bibliography=REFERENCES.bib \
  -o /tmp/pandoc_output.docx
```

**检查点**：如果 pandoc 报错或输出文件为空，**暂停展示错误信息，等用户决定**（修复源文件 / 换 CSL 样式 / 中止）。

pandoc 的 `--citeproc` 会将 `[@citekey]` 转为编号引用。根据 pandoc 版本和 CSL 样式，引用格式可能是上标（`^1,2^`）或方括号（`[1]`）。

**引用格式检测**（在 Step 6 之前执行）：

pandoc 输出的引用格式取决于 CSL 样式。运行脚本前先检测实际格式：
```bash
python3 -c "
from docx import Document
doc = Document('/tmp/pandoc_output.docx')
import re
from docx.oxml.ns import qn
superscript = 0
bracket = 0
for p in doc.paragraphs:
    for r in p.runs:
        rPr = r._element.find(qn('w:rPr'))
        if rPr is not None and rPr.find(qn('w:vertAlign')) is not None:
            if rPr.find(qn('w:vertAlign')).get(qn('w:val')) == 'superscript':
                if re.match(r'^\d+(,\d+)*$', r.text.strip()):
                    superscript += 1
        if re.match(r'^\[\d+(,\d+)*\]$', r.text.strip()):
            bracket += 1
print(f'superscript_citations={superscript}, bracket_citations={bracket}')
if bracket > 0 and superscript == 0:
    print('FORMAT: bracket')
elif superscript > 0:
    print('FORMAT: superscript')
else:
    print('FORMAT: unknown')
"
```

- **上标格式** → 直接运行 inject_zotero.py
- **方括号格式** → 需要修改脚本的 `is_superscript_citation_run` 函数为检测方括号模式（`\[\d+(,\d+)*\]`），或在 pandoc 命令中添加 `--csl` 指定上标 CSL 样式重新生成
- **未知格式** → 提示用户检查 MD 文件中的引用标记是否正确

### Step 6: 注入 Zotero field codes

1. 解析 BIB 文件，获取每个 cite_key 的 DOI 和 title。
   **BIB 文件必须是标准 BibTeX 格式**，使用 `bibtexparser` 库解析：
```bash
pip install bibtexparser
```
```python
import bibtexparser

def parse_bib(bib_path):
    """解析标准 BibTeX 文件，返回 {cite_key: {doi, title}}"""
    with open(bib_path, encoding='utf-8') as f:
        db = bibtexparser.load(f)
    entries = {}
    for entry in db.entries:
        entries[entry['ID']] = {
            'doi': entry.get('doi', '').strip() or None,
            'title': entry.get('title', '').strip() or None,
        }
    return entries
```
> **为什么用 bibtexparser**：BibTeX 是固定格式标准，`bibtexparser` 是 Python 生态的标准解析库，正确处理嵌套花括号、多行字段、字符串拼接等 BibTeX 语法，不需要手写正则。
运行参数化脚本：
```bash
python3 ~/.claude/skills/md2word-skill/scripts/inject_zotero.py \
  --input /tmp/pandoc_output.docx \
  --output FINAL_OUTPUT.docx \
  --mapping /tmp/citation_mapping.json \
  --user-id ZOTERO_USER_ID
```

脚本将上标的纯文本引用编号替换为 Zotero 原生的 `ADDIN CSL_CITATION` field code（5 个 XML 元素：begin → instrText → separate → display → end），删除静态 References 节，插入 `ADDIN ZOTERO_BIBLIOGRAPH` 占位符。这样 Zotero Word 插件就能识别并管理所有引用。

**用户确认点**：脚本运行后，展示替换统计（多少引用被替换、多少警告），确认数量是否合理。如果替换数量与预期不符，检查映射文件后再决定是否继续。

### 验证

```bash
python3 -c "
from docx import Document
import zipfile, re
with zipfile.ZipFile('OUTPUT.docx') as z:
    content = z.open('word/document.xml').read().decode()
    citations = re.findall(r'ZOTERO_ITEM CSL_CITATION', content)
    bibs = re.findall(r'ZOTERO_BIBLIOGRAPH', content)
    print(f'✓ {len(citations)} Zotero citation fields')
    print(f'✓ {len(bibs)} bibliography placeholder')
"
```

最终在 Word 中打开输出文件，Zotero 插件应自动识别 field codes 并管理引用。

## 注意事项

- **不要覆盖**现有文件，输出总是写到新路径
- **不要修改**现有 Zotero collection，除非用户明确要求
- 如果 BIB 中有条目在 Zotero 中找不到匹配，**暂停并报告**未匹配列表
- pandoc 版本需支持 `--citeproc`（pandoc 2.11+）
- 如果用户已有 Word 文件（不需要从 MD 转换），只需执行 Step 4（构建映射）和 Step 6（注入），跳过 pandoc

## 边界条件与异常处理

| 情况 | 处理方式 |
|------|----------|
| BIB 文件编码非 UTF-8 | 用 `iconv -f GBK -t UTF-8` 转码后再解析 |
| MD 中没有引用标记 `[@key]` | 跳过 Step 4-6，仅做 pandoc 转换 |
| Zotero collection 为空 | 提示用户先导入，暂停等待 |
| 同一 cite_key 在 BIB 中重复 | 取最后一条，并在未匹配报告中标注 |
| BIB 条目无 DOI 也无 title | 无法匹配，列入未匹配报告让用户手动指定 |
| pandoc 输出方括号 `[1]` 而非上标 | 见 Step 5 的格式检测逻辑 |
| Word 文件中没有找到上标引用 | 检查格式，可能需要调整检测逻辑 |
| Zotero item key 映射不完整 | 脚本会跳过未映射的引用并输出警告 |
