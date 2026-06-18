# Step 4: cite_key → Zotero item key 映射（由 Step 3 import 产出）

**映射现在由 Step 3 的 `import_zotero.py` 直接产出**，无需单独读 collection（省一次网络往返，也避免读 collection 时的网络抖动）。
import 在 create/update 每个 item 时，记录 cite_key → zotero_key，并标注**置信度与锚点**。

输出 `mapping.json`（默认 verify.json 同目录，可用 `--output-mapping` 指定），每条格式：
```json
{"cite_key": {"zotero_key": "ABCD1234", "anchor": "doi", "confidence": "high", "status": "PASS"}}
```

**置信度分级**（审计用，反映映射可靠性）：
- `high` / `doi` — DOI 精确锚点（最可靠，三源验证过）
- `medium` / `title` — 无 DOI，标题反查匹配
- `low` / `bib` — SKIP 条目用 bib 原值导入（未三源验证，需 `--import-skip`）

import 末尾会列出所有非 high 的低置信映射，**建议人工确认**。`inject_zotero.py` 兼容此新格式（也向后兼容旧的 `{ck: key}` 简单格式）。

**检查点**：查看 import 输出的低置信列表，确认 medium/low 映射是否可接受；不可接受的可手动修正 mapping.json 再跑 inject。
