---
name: md2word-skill
description: "从 Markdown / LaTeX / Typst + BibTeX 生成 Zotero 管理的 Word 文档。将 pandoc 引用 [@key]/\\cite{key}/Typst @key 转为 Zotero CSL_CITATION field codes。触发词: /md2word, md转word, latex转word, typst转word, markdown转word, zotero field codes, 参考文献管理, 论文格式化, BibTeX to Word, pandoc to Word, Zotero citation injection."
---

# /md2word: MD/LaTeX/Typst + BIB → Zotero-managed Word

## 前提条件

- **输入文件**：`.md`（pandoc `[@key]` / `@key` 引用）、`.tex`（`\cite{}`/`\citep{}`/`\citet{}` 等）或 **`.typ`**（Typst `@key` + `#bibliography()`）
- **BibTeX 文件**：包含所有引用的参考文献（每条最好有 DOI）
- **Zotero 桌面版**：正在运行，本地 API 已开启（`http://localhost:23119`）
- **Python 环境**：项目根目录下 `.venv/`（`uv venv` 创建）。首次运行 `bash run.sh` 或 `make setup` 自动安装；Windows 原生用 `run.bat`
- **依赖**：pandoc 3.6+（`--citeproc`，`.typ` 格式需要 3.6+；2.11+ 仍可用于 `.md`/`.tex`）、pyzotero、python-docx、lxml、bibtexparser（均在 `pyproject.toml` 中声明）
- **内置 CSL 样式**：`styles/physics-in-medicine-and-biology.csl`（dependent，parent 为同目录 `institute-of-physics-harvard.csl`）
- **Zotero Web API**（可选）：仅完整路径（BIB 未导入 Zotero 时）需要。配置 `ZOTERO_API_KEY` + `ZOTERO_USER_ID`（[zotero.org/settings/keys](https://www.zotero.org/settings/keys)）

## 工作流程

> **输出约定**：所有中间文件和最终输出默认保存到输入文件所在目录（`OUTDIR`）。最终文件名：`<文件名>_zotero.docx`。

> **执行约定**：每一步单独运行，打印进度后再进入下一步。所有 python 脚本通过 `bash run.sh scripts/...` 调用（自动使用 `.venv` 并屏蔽 proxychains）。

```
快速路径（默认，BIB 已在 Zotero 库中）: Step 1 → 2a+2b → 4(本地API) → 5 → 6   ≈ 20s
完整路径（BIB 不在 Zotero 中）:        Step 1 → 2a+2b+2c → 3 → 4 → 5 → 6     ≈ 100-180s
```

- **快速路径**（默认）：BIB 是从 Zotero 导出的，文献已在库中 → 跳过 Step 3，直接用本地 API 按 DOI 建映射
- **完整路径**（`--verify`）：BIB 来自外部，需三源核查后导入 Zotero → 走 Step 2c + Step 3
- 用户说「核查」「验证文献」「verify」时，走完整路径

| Step | 说明 | 详情文档 |
|------|------|----------|
| 1 | 收集参数 & 环境预检 | `docs/step1.md` |
| 2 | 依赖检查 + 交叉验证 [+ 三源核查与裁决] | `docs/step2.md` |
| 3 | 创建 Collection + 导入权威元数据（完整路径） | `docs/step3.md` |
| 4 | cite_key → Zotero key 映射 | `docs/step4.md` |
| 5 | pandoc 输入文件 → Word | `docs/step5.md` |
| 6 | 注入 Zotero field codes | `docs/step6.md` |

> **渐进式读取**：执行到哪步就读对应的 `docs/step-N.md`，不要一次性全部加载。

## 脚本调用方式

```bash
# Linux/macOS/WSL
bash run.sh scripts/verify_references.py INPUT.tex refs.bib
bash run.sh scripts/inject_zotero.py --input pandoc_output.docx ...

# Windows 原生
run.bat scripts\verify_references.py INPUT.tex refs.bib
run.bat scripts\inject_zotero.py --input pandoc_output.docx ...
```

首次运行自动创建 `.venv` 并安装依赖；也可手动：`make setup`（Linux/macOS）或 `uv venv .venv && uv pip install -r pyproject.toml`（Windows）。

## Step 4 快速路径：本地 API 建映射

BIB 从 Zotero 导出时，直接用本地 API 按 DOI 匹配建 mapping.json，无需 Web API：

```python
from pyzotero import zotero; import bibtexparser, json
zot = zotero.Zotero(0, 'user', local=True)
db = bibtexparser.load(open('refs.bib', encoding='utf-8'))
bib = {e['ID']: (e.get('doi','').strip().lower() or None) for e in db.entries}
items = zot.everything(zot.items(itemType='journalArticle'))
doi2key = {(it['data'].get('DOI') or '').strip().lower(): it['data']['key']
           for it in items if (it['data'].get('DOI') or '').strip()}
mapping = {ck: {'zotero_key': doi2key[doi], 'anchor': 'doi', 'confidence': 'high', 'status': 'PASS'}
           for ck, doi in bib.items() if doi and doi in doi2key}
json.dump(mapping, open('mapping.json','w'), ensure_ascii=False, indent=2)
```

## 注意事项

- **不要覆盖**现有文件，输出写到新路径
- pandoc 需 2.11+（支持 `--citeproc`）；`.typ` 输入需 pandoc 3.6+；`.tex` 输入时复杂 LaTeX 宏可能无法完美转换
- Typst 引用语法 `@key` 与 pandoc markdown `[@key]` 兼容，`#bibliography("refs.bib")` 等价于 `--bibliography`
- 已有 Word 文件只需注入 → 跳过 Step 5，直接 Step 4+6
- CSL 决定引用格式与注入策略；`inject_zotero.py` 自动检测 `citation-format`
- inject 注入后需在 WPS/Word 中点 Zotero **Add Bibliography** 插入参考文献列表，点 **Refresh** 重渲染引用格式

## 边界条件

| 情况 | 处理 |
|------|------|
| BIB 非 UTF-8 | `iconv -f GBK -t UTF-8` 转码 |
| 输入文件无引用 | 跳过 Step 4-6，仅 pandoc 转换 |
| cite_key 无 DOI | 用标题反查（confidence=medium），建议人工确认 |
| 条目无 DOI 无 title | 无法匹配，报告让用户指定 |
| CSL 是 dependent | 自动找父样式，找不到则报错 |
| CSL 不存在 | 列出已有样式，提示下载 |
| 映射不完整 | 跳过未映射引用，输出警告 |
| `\citet` 引用 | inject 自动向前合并作者 run，正确处理 |
| 组合引用第2+项哈希 anchor | inject 用文本反查 bib 匹配，正确处理 |
