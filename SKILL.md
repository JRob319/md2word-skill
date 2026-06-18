---
name: md2word-skill
description: "从 Markdown + BibTeX 生成 Zotero 管理的 Word 文档。将 pandoc 引用 [@key] 转为 Zotero CSL_CITATION field codes。触发词: /md2word, md转word, markdown转word, zotero field codes, 参考文献管理, 论文格式化, BibTeX to Word, pandoc to Word, Zotero citation injection."
---

# /md2word: MD + BIB → Zotero-managed Word

## 前提条件

- **Markdown 文件**：含 pandoc 风格引用（`[@key]` 或 `[@key1; @key2]`）
- **BibTeX 文件**：包含所有引用的参考文献（每条最好有 DOI）
- **Zotero 桌面版**：正在运行。读取用本地 API；**导入/写入必须用 Web API**（`ZOTERO_API_KEY` + `ZOTERO_USER_ID`，本地 API 不支持写）
- **依赖**：pandoc 2.11+（`--citeproc`）、pyzotero、python-docx、lxml、bibtexparser；三源核查用 CrossRef + PubMed + OpenAlex（均免费无 key）
- **内置 CSL 样式**：`styles/` 目录下已有 `physics-in-medicine-and-biology.csl`（dependent）和 `institute-of-physics-harvard.csl`（parent）。默认使用前者，用户指定其他样式时需提供路径或 URL。

## 工作流程

> **输出约定**：所有中间文件和最终输出默认保存到 md 与 bib 文件所在同一目录（下文记为 `OUTDIR`），除非用户指定其他路径。最终文件名：`<md文件名>_zotero.docx`。

> **执行约定**：每一步必须**单独运行**并在终端打印该步骤的进度和结果后再进入下一步。禁止将多步合并为一条命令静默执行。每步执行前用 `echo` 或 `print` 显示当前步骤编号和目标。

```
快速路径（默认）:  Step 1 → 2a+2b → 3 → 4 → 5 → 6    ≈ 40s
完整路径（--verify）: Step 1 → 2a+2b+2c → 3 → 4 → 5 → 6  ≈ 100-180s
```

- **快速路径**：跳过 Step 2c（三源核查），直接导入。适合信任 BIB 质量的场景。
- **完整路径**：运行 CrossRef + PubMed + OpenAlex 三源核查，用权威元数据导入（修正 BIB 错误）。适合投稿前终审。
- 用户未明确要求时，**默认走快速路径**。
- 用户说「核查」「验证文献」「verify」时，走完整路径。

| Step | 说明 | 详情文档 |
|------|------|----------|
| 1 | 收集参数 & 环境预检 | `docs/step1.md` |
| 2 | 依赖检查 + 交叉验证 [+ 三源核查与裁决] | `docs/step2.md` |
| 3 | 创建 Collection + 导入权威元数据 | `docs/step3.md` |
| 4 | 映射（import 产出，含置信度/审计） | `docs/step4.md` |
| 5 | pandoc MD → Word | `docs/step5.md` |
| 6 | 注入 Zotero field codes | `docs/step6.md` |

> **渐进式读取**：执行到哪步就读对应的 `docs/step-N.md`，不要一次性全部加载。

---

## 注意事项

- **不要覆盖**现有文件，输出写到新路径
- **不要修改**现有 Zotero collection，除非用户明确要求
- pandoc 需 2.11+（支持 `--citeproc`）
- 已有 Word 文件只需注入 → 跳过 Step 5，直接 Step 4+6
- CSL 是工作流核心——决定引用格式、参考文献列表格式、注入策略
- **导入用权威元数据，不信任 BIB**：Step 3 用 verify 三源裁决后的 CrossRef/PubMed/OpenAlex 数据（修正 BIB 里作者名/年份等错误）。⚠️ 不要用「只填 DOI 让 Zotero 自动补全」——pyzotero（local/Web API）不触发补全，实测得到的是只有 DOI 的残缺条目

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
