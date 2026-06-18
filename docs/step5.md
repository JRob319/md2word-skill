# Step 5: pandoc MD → Word

```bash
pandoc INPUT.md --citeproc -M link-citations=true --bibliography=REFERENCES.bib --csl=CSL_PATH -o OUTDIR/pandoc_output.docx
```

`CSL_PATH` 默认为本 skill 的 `styles/physics-in-medicine-and-biology.csl`。它是 dependent style，pandoc 会自动在同目录找到 parent `institute-of-physics-harvard.csl`。

> **`-M link-citations=true` 必加**：让 pandoc 把引用渲染成 `<w:hyperlink w:anchor="ref-{cite_key}">`。Step 6 的 inject 据此 anchor **精确**定位 cite_key（同年同作者多篇也能区分）。若漏加，inject 退回文本匹配，同年同作者会误关联。

pandoc 后自动检测 CSL 的 `citation-format`（解析 XML 中 `<category citation-format="...">`）：
- `author-date` → Step 6 用 Author+Year 匹配
- `numeric` → Step 6 用编号匹配
- `note` → Step 6 用脚注标记匹配

**检查点**：pandoc 报错或输出为空时暂停。
