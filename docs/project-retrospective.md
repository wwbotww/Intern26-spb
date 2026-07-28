# 国家邮政局“政策法规标准”知识库项目复盘

> 复盘日期：2026-07-28  
> 数据来源：国家邮政局“政策法规标准”栏目  
> 目标库：Milvus `aisv.spb_policy_chunks`  
> Dense Embedding：`moka-ai/m3e-base`

## 一、项目概述

本项目的目标是将国家邮政局网站“政策法规标准”栏目中的页面正文和附件完整归档，经过结构化解析、文本切分、向量化后写入公司内网 Milvus，为后续政策检索、标准查询、RAG 问答和知识库应用提供稳定的数据基础。

项目并不是一次性的网页复制，而是建设了一条可以重复运行、断点续跑、追踪错误和增量同步的数据流水线：

```mermaid
flowchart LR
    A["栏目检索接口"] --> B["292 条全量目录"]
    B --> C["详情页断点抓取"]
    C --> D["多模板正文解析"]
    D --> E["附件发现与下载"]
    E --> F["PDF / DOCX / DOC 解析"]
    F --> G["扫描件 Vision OCR"]
    G --> H["结构化文档与质量报告"]
    H --> I["模型适配分块"]
    I --> J["m3e-base 768 维向量"]
    J --> K["Milvus Dense + BM25"]
```

## 二、最终产出

### 2.1 数据产出

| 指标 | 最终结果 |
|---|---:|
| 栏目目录 | 292 条 |
| 页面文档 | 292 个 |
| 页面正文覆盖率 | 292/292 |
| 发现附件 | 144 个 |
| 成功归档附件 | 139 个 |
| 失效附件 URL | 5 个 |
| OCR 扫描附件 | 38 个 |
| OCR 页数 | 500 页 |
| 有正文的文档 | 431/436 |
| 最终 chunks | 12,163 条 |
| Dense 向量维度 | 768 |
| 自动化测试 | 15 项全部通过 |
| 本地数据规模 | 约 569 MB |

一个旧版详情页返回 HTTP 502，但栏目接口仍保留正文，因此通过 API 内容完成了兜底。最终 292 个页面文档均有正文和分块。

剩余 5 个空文档均为历史附件地址失效，表现为重复扩展名 URL 返回 404 或零字节文件。其父页面正文已经入库，失败附件也保留了来源、父文档和错误状态，没有被静默丢弃。

### 2.2 工程产出

项目形成了以下可复用能力：

- 全量栏目目录发现及分页完整性校验；
- 按域名限速、重试、断点续跑和错误状态记录；
- 多种国家邮政局页面模板及外链页面解析；
- HTML、表格、PDF、DOCX、旧版 Word 和图片附件处理；
- macOS Vision 中文 OCR 及 OCR sidecar 断点机制；
- 面向法规章节、条款和表格的结构化切分；
- m3e-base 向量生成、缓存复用和输入长度校验；
- Milvus collection 安全创建、首次写入和增量同步；
- HNSW Dense 检索与 Milvus 内置 BM25 Sparse 检索；
- 数据覆盖率、空文档、重复项、抓取状态和索引状态检查。

主要代码位置：

- `src/spb_pipeline/inventory.py`：栏目目录；
- `src/spb_pipeline/crawler.py`：详情页和附件抓取；
- `src/spb_pipeline/parser.py`：网页解析；
- `src/spb_pipeline/attachments.py`：附件解析；
- `src/spb_pipeline/ocr.py`：OCR 流程；
- `src/spb_pipeline/chunker.py`：法规文本切分；
- `src/spb_pipeline/embedding.py`：向量生成与缓存；
- `src/spb_pipeline/sinks/milvus.py`：Milvus schema 和写入；
- `src/spb_pipeline/quality.py`：质量报告；
- `src/spb_pipeline/cli.py`：统一命令行入口。

## 三、实施过程

### 3.1 页面访问与数据源确认

项目初期无法直接访问目标网站。排查后确认问题来自访问权限和运行环境，而非页面本身。用户开放 CDP 和站点访问权限，并提供本地 HTML 样本后，先用浏览器检查页面，再定位到栏目实际使用的 JSON 检索接口。

最终以接口中的栏目代码 `c100012` 作为数据边界，而不是根据 URL 路径猜测归属。这样可以保留栏目中的中国政府网、微信和旧版国家邮政局外链，避免因为域名不同而漏数。

接口声明总数为 292，流水线只有在实际去重后仍得到 292 条记录时才接受目录结果，从源头避免分页不完整。

### 3.2 详情页抓取与模板解析

详情页并不使用单一模板，实际发现了：

- 当前普通详情页；
- 旧版 `.TRS_Editor` 页面；
- 政府信息公开页面；
- 中国政府网新旧页面；
- 微信文章；
- 无法下载时的接口正文兜底。

解析器先识别模板和正文容器，再提取标题、正文块、表格、文号、发布机构、附件和相关解读。正文使用 block 模型保存，而不是直接压缩为一个字符串，从而保留标题、条款、列表、表格和页码等结构。

全量抓取结果为 291 个详情页成功、1 个旧站链接失败。失败记录没有从结果集中删除，而是使用接口正文生成文档，并在抓取状态库中保留 502 错误。

### 3.3 附件发现与归档

仅依赖接口的 `resList` 会漏掉详情页表格和附件区域中的大量文件，因此项目同时扫描：

- 页面正文内的文件链接；
- `.fujian` 等附件区域；
- 标准目录表格中的 PDF；
- 接口 `resList`。

附件通过稳定哈希生成 ID，并保存父文档关系。抓取状态使用 SQLite 保存，记录 HTTP 状态、内容类型、长度、哈希、本地路径、重试次数和错误信息。

项目还处理了网站历史数据中的异常：

- `.pdf.pdf`、`.doc.doc` 等重复扩展名；
- HTTP 200 但响应体为零字节；
- 文件名是 `.docx`，实际内容却是旧 OLE Word；
- 页面标题和真实文件类型不一致。

对于重复扩展名，原 URL 失败或返回空文件时会尝试去掉一个扩展名；对零字节响应直接判为失败，不再把它当作成功归档。

### 3.4 附件解析和 OCR

附件解析策略如下：

| 类型 | 处理方式 |
|---|---|
| PDF 文本版 | `pypdf` 按页提取 |
| DOCX | `python-docx` 提取段落和表格 |
| XLSX | `openpyxl` 读取工作表 |
| 旧版 DOC | macOS `textutil` 转换 |
| 扫描 PDF / 图片 | Poppler 渲染 + macOS Vision OCR |

部分扩展名为 `.docx` 的文件实际是 Compound Document File。项目通过文件头魔数识别旧 Word，而不是只信任文件扩展名，从而恢复了 3 个原本解析失败的附件。

文本型 PDF 无法提取内容时，转入 OCR 队列。OCR 结果不直接覆盖原始文件，而是按 attachment ID 写入 `data/processed/ocr/`。每个附件独立保存，任务可以中断后继续，已经识别成功的文件不会重复处理。

最终 38 个扫描附件、500 页内容全部完成 OCR，OCR 待处理数从 38 降为 0。

### 3.5 文本切分与模型适配

初始切分上限为 1,200 字符，适合通用文本归档，但不适合 `moka-ai/m3e-base`。该模型的输入上限为 512 tokens，直接使用原始分块会产生静默截断。

项目将切分调整为：

- 最大 360 字符；
- 重叠 50 字符；
- 章节、条款和表格行作为硬边界；
- embedding 输入附带标题、文号和章节上下文。

调整后使用模型 tokenizer 对全部输入进行实测：

| 项目 | 结果 |
|---|---:|
| 模型上限 | 512 tokens |
| 实际最大值 | 419 tokens |
| P99 | 小于模型上限 |
| 超限输入 | 0 |

这一检查避免了“向量生成成功，但正文后半段从未进入模型”的隐性质量问题。

### 3.6 向量生成

Dense 模型选择 `moka-ai/m3e-base`，主要原因是：

- 面向中文语义表示；
- 输出维度为 768，与公司现有知识库向量维度一致；
- 可通过 `sentence-transformers` 本地运行；
- 支持归一化后使用 COSINE 相似度；
- 无需依赖外部在线 embedding API，适合内网环境。

向量生成使用 Apple MPS 加速，最终形状为 `(12163, 768)`，数据类型为 `float32`。所有向量执行 L2 归一化，范数误差在浮点正常范围内。

为降低增量成本，embedding artifact 按 chunk ID 复用已有向量。OCR 加入后，12,163 个 chunks 中：

- 复用原向量：11,034 条；
- 新生成向量：1,129 条。

因此补充 OCR 时不需要重新计算整个知识库。

### 3.7 Milvus 接入

接入前先以只读方式检查数据库：

- Milvus 版本：2.6.7；
- 可见数据库：`default`、`aisv`；
- `aisv` 原有 6 个 collection；
- Dense 向量均采用 768 维、HNSW、COSINE；
- 多个业务 collection 已使用 Milvus 内置 BM25。

现有 `aisv_zsk_msg` 虽然接近知识库用途，但字段长度、元数据字段和动态字段设置均与本项目不兼容，因此没有复用或修改现有 collection，而是创建独立的：

```text
aisv.spb_policy_chunks
```

关键 schema 包括：

- `id`、`document_id`、`parent_document_id`；
- `title`、`text`、`embedding_text`；
- `text_dense`：768 维 FLOAT_VECTOR；
- `text_sparse`：BM25 生成的 SPARSE_FLOAT_VECTOR；
- `source_url`、`source_host`；
- `published_at`、`document_no`、`source_org`；
- `validity_status`、`section_path`、`chunk_index`；
- `content_hash`、`fetch_status`。

索引设计：

| 字段 | 索引 |
|---|---|
| `text_dense` | HNSW + COSINE |
| `text_sparse` | SPARSE_INVERTED_INDEX + BM25 |
| `document_id` | AUTOINDEX |
| `parent_document_id` | AUTOINDEX |
| `published_at` | AUTOINDEX |
| `validity_status` | AUTOINDEX |

首次写入前强制要求 collection 为空；增量同步只插入数据库中不存在的 chunk ID。创建逻辑如果发现同名 collection 已存在，会拒绝覆盖，不执行 drop 或重建。

Milvus 写入后存在统计和索引异步可见的问题。项目通过以下方式交叉核验：

1. 客户端累计写入数量；
2. 强一致性 `count(*)`；
3. collection stats；
4. 仅对新 collection 执行 flush；
5. 轮询各索引的 `indexed_rows` 和 `pending_index_rows`。

最终结果：

- `row_count = 12163`；
- 所有索引 `state = Finished`；
- 所有索引 `indexed_rows = 12163`；
- `pending_index_rows = 0`；
- collection 状态为 `Loaded`。

原有 6 个 collection 的记录数在写入前后完全一致，确认没有受到影响。

## 四、主要技术难点及解决方案

| 难点 | 风险 | 解决方案 |
|---|---|---|
| 初始无法访问网站 | 无法确认真实页面结构 | 开放 CDP/站点权限，结合本地 HTML 和浏览器检查 |
| 栏目存在外链和多域名 | 按 URL 路径过滤会漏数 | 以接口栏目代码和 manuscript ID 为准 |
| 页面模板多样 | 单一 CSS selector 大量空正文 | 模板识别、多容器解析、API 兜底 |
| 附件来源分散 | 只用 `resList` 会漏附件 | HTML 正文、附件区、表格和 API 联合发现 |
| 历史链接异常 | 404、零字节、重复后缀 | URL 修复、零字节失败判定、完整错误记录 |
| 扩展名不可信 | DOCX 解析器报错 | 文件魔数识别 + 旧 Word 转换 |
| 500 页扫描文档 | 普通 PDF 提取为空 | Poppler 渲染 + Vision 中文 OCR |
| 模型输入截断 | 向量缺失正文后半段 | tokenizer 全量校验，chunk 调整到 360/50 |
| 数据库已有业务数据 | 误写或覆盖风险 | 只读审计、独立 collection、拒绝覆盖 |
| Milvus 异步统计 | 写入数量和 stats 短暂不一致 | 强一致 count、定向 flush、索引状态轮询 |
| OCR 后增量成本高 | 全量重复计算向量 | 基于稳定 chunk ID 复用 11,034 条向量 |

## 五、技术栈选择及原因

| 技术 | 用途 | 选择原因 |
|---|---|---|
| Python 3.11 | 主开发语言 | 文本、HTTP、文档解析和 Milvus 生态完整 |
| uv | 依赖和虚拟环境 | 锁定依赖、安装速度快、可复现 |
| httpx | HTTP 抓取 | 超时、重试、重定向和流式响应处理方便 |
| BeautifulSoup + lxml | HTML 解析 | 兼容历史页面，容错性高 |
| SQLite | 抓取状态 | 无额外服务、支持断点和错误审计 |
| pypdf | PDF 文本提取 | 轻量、可按页保留结构 |
| python-docx | DOCX 解析 | 可读取段落和表格 |
| openpyxl | XLSX 解析 | 保留工作表和表格结构 |
| Poppler `pdftoppm` | 扫描 PDF 渲染 | 稳定、页级输出、适合 OCR |
| macOS Vision | 中文 OCR | 本机可用、中文效果好、无需额外模型服务 |
| sentence-transformers | Embedding | 可直接加载 m3e-base，支持批处理和 MPS |
| NumPy NPZ | 向量缓存 | 体积小、读取快、便于增量复用 |
| Milvus 2.6.7 | 向量检索 | 支持 Dense、Sparse、BM25 和标量过滤 |
| pytest | 自动化测试 | 覆盖模板、切分、附件、状态和异常 URL |

## 六、项目亮点

### 6.1 完整性优先，而不是“抓到多少算多少”

目录数量、页面覆盖率、附件状态、空文档、重复 ID 和 chunk 覆盖均有显式检查。抓取失败不会被从数据集中删除，而是作为可追踪异常保留。

### 6.2 原始数据、结构化数据和向量数据分层

原始 JSON、HTML、附件、OCR sidecar、documents、chunks 和 embeddings 分开保存。任何一层出现问题都可以单独重跑，不需要重新抓取全部数据。

### 6.3 稳定 ID 和内容哈希

document、attachment 和 chunk 均使用稳定哈希 ID。chunk ID 同时包含文档内容哈希、章节路径和序号，既支持去重，也为增量同步和向量缓存提供基础。

### 6.4 法规结构感知切分

切分不是简单按固定长度截断，而是识别章、节、条、段落和表格行。检索结果能够返回“第三章/第十七条”等章节上下文，更适合法规问答。

### 6.5 Dense 与 BM25 双通道

Dense 检索负责语义召回，BM25 负责法规名称、术语、标准号和精确关键词召回。实际验证中，两种检索均返回了与查询高度相关的政策和 OCR 标准内容。

### 6.6 数据库安全边界清晰

接入阶段先只读检查，再创建专用 collection。程序拒绝覆盖同名 collection，首次写入要求目标为空，增量操作只插入缺失 ID，flush 也只针对新 collection。

### 6.7 OCR 可续跑且可审计

OCR 结果是独立 sidecar，而不是不可逆地写回原文件。可以定位到具体附件和页码，也便于后续替换为更高质量 OCR 引擎。

## 七、测试与验证

自动化测试共 15 项，覆盖：

- 栏目记录规范化；
- 普通、公开、中国政府网和微信页面模板；
- API 正文兜底；
- HTML 表格和附件发现；
- 法规条款硬边界切分；
- 表格按行切分；
- DOCX、XLSX 解析；
- SQLite 状态记录；
- 重复扩展名 URL 修复。

端到端验证包括：

- 292 条目录数量校验；
- 292/292 页面文档覆盖；
- embedding chunk ID 唯一性；
- embedding shape、dtype 和范数检查；
- tokenizer 超限检查；
- Milvus 强一致 count；
- 全部索引完成状态；
- Dense 实际查询；
- BM25 实际查询；
- OCR 标准正文实际查询；
- 原业务 collection 写入前后数量比对。

## 八、遗留问题和风险

### 8.1 五个源站附件已失效

这 5 个附件在官网返回 404 或零字节，无法从当前页面恢复。建议后续：

- 查询网页历史快照或国家标准公开平台；
- 联系业务方确认是否有内部归档；
- 若取得替代文件，使用相同父文档关系补录。

这也是质量报告保持 `ready_for_milvus=false` 的唯一主要原因。当前有效内容已经写入并可检索，但严格意义上尚未达到 436/436 文档均有正文。

### 8.2 Milvus 未提供认证

本次未配置 token 也能读取数据库元数据和写入新 collection。需要确认这是预期的内网信任策略，还是服务尚未启用认证。建议至少配置：

- Milvus 用户认证；
- 网络 ACL；
- 项目专用账号；
- 最小权限；
- 写入审计。

### 8.3 OCR 依赖 macOS

当前 OCR 使用 macOS Vision，旧 Word 转换使用 `textutil`。若流水线部署到 Linux，需要替换为 PaddleOCR、Tesseract、LibreOffice 或内部文档转换服务。

### 8.4 增量删除和更新策略尚未自动化

当前 `milvus-sync` 只插入缺失 chunk。如果上游文档内容发生变化，新的 chunk ID 会加入，但旧 chunk 需要根据 document ID 和 content hash 主动清理。正式定时任务应增加：

- 文档版本表；
- 旧 chunk 软删除或定向删除；
- 数据快照与回滚；
- 增量运行报告。

### 8.5 OCR 结果仍可能包含版面噪声

表格、页眉、页脚、水印和特殊符号可能产生 OCR 噪声。当前结果已具备检索价值，但若用于高精度条款引用，建议增加版面分析、页眉页脚去重和抽样人工验收。

## 九、经验总结

1. 爬虫项目首先要定义完整的数据边界，不能把“能访问的 URL”误认为“全部数据”。
2. 政府网站历史跨度大，页面模板、编码、文件类型和链接质量必须按不可信输入处理。
3. 原始文件应永久保留，解析结果和 OCR 结果应可重建。
4. 模型维度匹配不等于模型适配，输入 token 上限同样必须实测。
5. Milvus 写入成功、统计可见和索引完成是三个不同阶段，需要分别验证。
6. 对已有数据库进行接入时，最重要的不是写入速度，而是明确变更边界和可验证的不影响承诺。
7. 稳定 ID、内容哈希和本地缓存能够显著降低增量更新成本。

## 十、后续建议

建议按以下顺序继续建设：

1. 为 Milvus 启用认证并配置项目专用最小权限账号；
2. 为 5 个失效附件寻找替代来源；
3. 增加检索服务 API，组合 Dense、BM25 和标量过滤；
4. 设计 hybrid ranker，对 Dense 与 Sparse 结果进行融合；
5. 增加增量更新、旧版本清理和运行审计；
6. 建立 OCR 与解析质量抽检集；
7. 接入 RAG 应用并增加政策来源、文号和原文链接引用；
8. 将抓取、解析、向量化和同步拆分为可调度任务。

## 十一、常用运维命令

```bash
# 更新目录和详情页
uv run spb-pipeline inventory
uv run spb-pipeline crawl-details

# 解析、附件和 OCR
uv run spb-pipeline parse
uv run spb-pipeline crawl-attachments
uv run spb-pipeline ocr
uv run spb-pipeline parse

# 模型适配切分和向量
uv run spb-pipeline chunk --max-chars 360 --overlap-chars 50
uv run spb-pipeline embed --model moka-ai/m3e-base

# 质量检查
uv run spb-pipeline report

# Milvus 增量同步与只读检查
uv run spb-pipeline milvus-sync \
  --uri http://milvus-host:19530 \
  --database aisv \
  --collection spb_policy_chunks

uv run spb-pipeline milvus-check \
  --uri http://milvus-host:19530 \
  --database aisv \
  --collection spb_policy_chunks
```
