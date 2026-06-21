# Step 5: pandoc 输入文件 → Word

支持 `.md` 和 `.tex` 两种输入，pandoc 自动根据扩展名识别格式：

```bash
pandoc INPUT_FILE --citeproc -M link-citations=true --bibliography=REFERENCES.bib --csl=CSL_PATH -o OUTDIR/pandoc_output.docx
```

`.tex` 输入时 pandoc 会识别 `\cite{}`/`\citep{}`/`\citet{}` 等命令，与 MD 的 `[@key]` 等价处理。

> **注意**：pandoc 对复杂 LaTeX（自定义宏、`tikz`、复杂表格）支持有限，内容会尽力转换但格式可能需要手动调整。结构性内容（正文、引用）通常无问题。

`CSL_PATH` 默认为本 skill 的 `styles/physics-in-medicine-and-biology.csl`。它是 dependent style，pandoc 会自动在同目录找到 parent `institute-of-physics-harvard.csl`。

> **`-M link-citations=true` 必加**：让 pandoc 把引用渲染成 `<w:hyperlink w:anchor="ref-{cite_key}">`。Step 6 的 inject 据此 anchor **精确**定位 cite_key（同年同作者多篇也能区分）。若漏加，inject 退回文本匹配，同年同作者会误关联。

pandoc 后自动检测 CSL 的 `citation-format`（解析 XML 中 `<category citation-format="...">`）：
- `author-date` → Step 6 用 Author+Year 匹配
- `numeric` → Step 6 用编号匹配
- `note` → Step 6 用脚注标记匹配

**检查点**：pandoc 报错或输出为空时暂停。
