# 原前后端与当前 Agent 对照分析

日期：2026-10-19。基于源码静态检查，未启动原系统、连接部署数据库或调用真实模型；功能实现不等同于已验证的线上可用性。

## 结论

同级项目分别为 `E:/agant/-RAG-Database`（Django 后端）与 `E:/agant/RAG-AI-system`（Vue 前端）。原后端已经实现真实模型调用、ASR、文档抽取、向量化、三档 RAG、流式问答、出题与追问，README 和部分模型文件的“演示占位/TODO”说明已经落后于源码。

当前 Agent 是独立的工具调度和能力库，重写了相当一部分原 AI 服务，并增强校验、会话状态、OCR 和失败处理。它尚未接入原系统，不能直接替换原后端。此前基于提示词文档而未读取原后端源码的实现，产生了重复能力；后续应以现有 Django 数据和业务接口为主体进行整合。

## 现有调用链

原上传链：Vue 创建纪要 → Django NoteCreateSerializer 校验并保存文件 → start_processing 后台线程 → 抽取/ASR → LLM 单次或 map-reduce 纪要 → 写回 Note → rag.index_note → NoteChunk。

原问答链：Vue `/api/qa/ask/stream/` → JWT 身份及纪要归属 → 向量检索 → 按 0.42/0.22 分档 → 必要时搜索及审核 → 模型流式正文 → qwen-flash 追问 → QaRecord 入库 → done 返回完整记录。

当前 Agent：宿主/CLI 登记资产 → Ingestor 抽取/ASR → 分块/嵌入 → SQLite；问题 → LangGraph 调度模型 → answer_question → 检索及业务模型流式生成 → 校验/替换/来源/追问 → 工具结果。没有自动回写原 Note、QaRecord、QuizItem。

## 功能对照

| 维度 | 原后端 | 当前 Agent | 实际影响 |
|---|---|---|---|
| 入口决策 | URL 和页面确定任务，Python 固定流程 | 模型选择工具，工具内部仍是固定流程 | 自然语言统一入口更方便；固定任务额外调度增加延迟/费用 |
| 模型 | qwen-plus、qwen-flash、qwen3-vl-plus、paraformer-v2、text-embedding-v4 | 默认同组模型，可通过环境变量调整 | 主要差异是编排、校验和数据连接，没有训练新模型 |
| RAG | Top-K=4，0.42/0.22 分档，0.05 最低过滤；外部白名单 | Top-K=4，同样分档，外部白名单并丢弃 low 来源 | 核心问答策略重复；新检索粒度需重新评估阈值 |
| 分块 | 纪要知识点/术语/重点 + 转写；非时间戳文字约 280 字聚合 | 原始抽取片段，最长 800 字、100 字重叠；短片段各自成块 | 新版不会自动把生成后的知识点和定义加入索引 |
| 向量存储 | Django NoteChunk.embedding JSONField，Python/NumPy 精确余弦 | SQLite JSON 向量，Python 精确余弦 | 两者都不是 pgvector/ANN；另起 SQLite 并不自动获得更高检索性能 |
| 向量校验 | 索引排序、总数检查 | 模型/维度签名、索引唯一性、有限值/非零检查 | 新版更易发现损坏或混用向量 |
| 本地录音 | 自动获取百炼临时上传策略 → OSS 上传 → oss:// ASR | 输入已可访问 HTTPS URL → ASR | 新版仍需复用原上传能力，否则本地录音入口退化 |
| ASR 等待 | 默认约 20 分钟轮询 | 默认约 5 分钟，声道 0，中英提示 | 长课录音超时策略需按原业务调整 |
| 文档格式 | TXT/MD/Markdown、PDF、DOCX、PPTX/PPSX、XLSX、CSV、ZIP；图片含 GIF | TXT/MD、PDF、DOCX、PPTX、PNG/JPEG/WebP；DOC/PPT 依赖 LibreOffice | 新版缺 XLSX/CSV/ZIP/PPSX/GIF/.markdown 的对应入口，不能替换原白名单 |
| 视频 | 原代码将 video 走 ASR 音轨处理 | 无本地视频登记/处理分支 | 两者都未实现抽帧融合；新版也未保留原视频入口 |
| PDF/OCR | 逐页读文字，无文字层整份无结果则要求上传截图；混合空页跳过 | 无文字层页自动渲染 OCR，保留页码 | 新版加强扫描件支持，但混合页面图片仍不保证识别 |
| DOCX/PPTX | DOCX 段落后追加表格；PPT 文本/表格 | DOCX 按块顺序，PPT 分组/备注，嵌图 OCR | 新版保留更多结构，但 OCR 失败阻止整份入库，可能降低容错 |
| 上传限制 | 文档/PPT 100MB，图片20MB，音频500MB，视频1024MB | 本地文件50MB，PDF/PPT300页 | 直接替换会拒绝原前端允许的部分材料 |
| 纪要闭环 | 上传后自动总结并写回 Note，更新处理状态 | 入库和 summarize_notes 是两件事，总结返回 dict | 不会自动出现原工作台需要的纪要字段和 done 状态 |
| 题库闭环 | 生成 QuizItem，规则降级；作答判分、WrongRecord、列表管理 | 严格校验题型/答案/来源，返回 questions/missing_count | 新版没有题库持久化、答题和错题本；失败可返回零题 |
| 问答持久化 | QaRecord、点赞、历史、管理员巡检 | Graph 检查点可选 PG，默认内存 | 检查点不能代替业务记录；新版无原历史/点赞的 ID |
| 追问 | 查询用户跨纪要近期 QaRecord，按日期和最新记录缓存，规则兜底 | 当前会话最近6条 human 消息 + 当前材料，无同等跨纪要缓存 | 全站推荐不能直接改成当前会话工具 |
| 权限 | Django JWT，上传者/管理员规则 | 可信 UserContext，tenant/user/note 隔离；宿主提供认证 | 原用户表没有 tenant 字段；需定义租户映射并保留管理员权限 |
| 记忆/偏好 | 保存历史记录，但 RAG 生成只传本轮问题及片段 | 调度模型读取 checkpoint 历史，用户偏好进入回答 | 业务回答模型仍未直接收到历史；省略式追问靠调度模型改写，效果未验证 |
| 失败处理 | 模型/检索故障可退回关键词演示检索；部分流保留并入库 | 检查错误引用并替换；远程嵌入失败通常返回服务不可用 | 新版更谨慎，但离线可用性不全面优于原版 |

## 当前 Agent 的编排边界

`prompts.py` 明确要求每轮一个工具，工具使用 `return_direct=True`，真实调度模型禁用并行调用。这适合选择问答/总结/出题任务，但不是多步骤自主执行器。

“导入这个文件，再整理纪要并出五道题”目前不能保证一轮执行完。入库器只到 indexed；summarize_notes/generate_quiz 另行调用，也没有 Django 回写适配。LangGraph 的会话历史能用于工具选择，但 answer_question 没有代码层的历史问题改写，不能宣称已具备可靠的多轮 RAG。

对已明确任务的旧按钮，不建议先调用调度模型再调用业务模型；可直接复用 `StudyService`，将工具调度留给统一聊天入口。

## 前后端合同不兼容

| 项目 | 当前前端及原后端 | 当前 Agent |
|---|---|---|
| URL | `/api/qa/ask/stream/` | 可挂载 `/agent/stream` |
| 请求 | `{note_id?, question}` | `{message, thread_id}`，纪要由认证依赖提供 |
| 事件 | delta / done | status / delta / replace / sources / followups / done |
| done | `ApiQaRecord`：id/note/note_title/course/answer/references/followups/liked 等 | `{output: {...}, model_call_count}` |
| 引用 | `{label,time,detail,kind:'kb'|'external',url?}` | `{id,title,text?,timestamp_seconds?,kind:'classroom'|'external',url?}` |
| 纪要知识点 | `{text,ref}` | `{text,ts?}` |
| 出题结果 | 已持久化 QuizItem 列表，含 id/course/chapter 等 | 未持久化的 questions + missing_count/degraded |

前端 `src/api/index.ts::streamAsk` 只处理 delta 和 done；QAPage 使用流式缓冲显示正文，并直接映射 `r.references`。接上 Agent 后若不适配，`replace` 会被忽略，最终引用/记录 ID 等也无法按原结构读取，可能产生运行错误或展示未校验正文。即使通过适配层保持原 done 结构，前端仍需处理 replace，或在完成时用最终 answer 覆盖缓冲。

## 建议的整合方案

保留 Django 为唯一业务后端和 PostgreSQL 数据源，将 Agent 接入其内部，不为同一套课堂材料长期维护另一份 SQLite 主数据。

1. 实现 Django Backend 适配器：`material` 读取已授权 Note，`retrieve` 复用/增强 NoteChunk 检索，`profile` 读取当前 Django 用户。Context 从 JWT 认证结果构造；为现有单租户系统定义固定服务端 tenant，管理员访问保持原规则。
2. 保留原上传处理器和临时 OSS 流程、格式白名单、500MB音频/1GB视频等业务限制；按格式选择原抽取器或 Agent 增强版。不能删掉 XLSX/CSV/ZIP 等现有能力。
3. 将 Agent 的引用/输出/向量校验并入原流程；改造生成服务时同步转换 ts→ref，写回 Note、QaRecord、QuizItem。避免“工具返回成功但业务表为空”。
4. 保留现有 `/api/qa/ask/stream/` 与 JWT；Django 将 Agent 事件转为原 SSE 合同并保存最终结果。前端新增 replace 支持，done 仍返回完整 QaRecord。
5. 统一模型配置、嵌入批次和维度元数据；避免同一文件被两套流水线重复 ASR、重复生成和重复嵌入。分块改变后重新评测召回阈值，不只照抄0.42/0.22。
6. 统一删除/重处理和任务状态；生产后台任务宜用可恢复队列。原 daemon 线程与新版同步入库都不能提供完整的持久任务恢复/自动重试。
7. 在确需复合任务时增加明确多步骤编排、业务事务/幂等写入及对话问题改写；这属于后续能力，当前工具路由尚未实现。

## 另外发现的原实现问题

- `process_note` 先写 `status=done`，再调用 `rag.index_note`，且没有检查其 False 返回值。因此 done 不保证向量索引已成功；后续检索会尝试懒索引。
- `index_note` 的 delete + bulk_create 未显式包裹原子事务，写入阶段失败可能留下缺失索引；新版同资产替换使用 SQLite 事务。
- 原流式异常会保留半段正文并保存 QaRecord，不标记结构化 degraded；新版使用权威替换。两种策略需要产品层明确选择。
- 原 SuggestionsView 在没有近期提问时查询最新纪要未限定 uploader，可能向其他用户展示纪要标题；整合时应补权限过滤。

## 关键源码位置

- 原模型适配：[llm.py](E:/agant/-RAG-Database/mysite/mysite/llm.py:25)
- 原材料流水线：[services.py](E:/agant/-RAG-Database/mysite/notes/services.py:497)
- 原 RAG 与分块：[rag.py](E:/agant/-RAG-Database/mysite/mysite/rag.py:29)
- 原流式业务落库：[qa/views.py](E:/agant/-RAG-Database/mysite/qa/views.py:205)
- 原题库落库：[quiz/views.py](E:/agant/-RAG-Database/mysite/quiz/views.py:187)
- 原材料格式与限制：[serializers.py](E:/agant/-RAG-Database/mysite/notes/serializers.py:28)
- 前端流协议：[api/index.ts](E:/agant/RAG-AI-system/src/api/index.ts:145)
- 前端回答渲染：[QAPage.vue](E:/agant/RAG-AI-system/src/views/QAPage.vue:574)
- Agent 路由与流：[runtime.py](E:/agant/-RAG-Database/agent/src/agent/runtime.py:84)
- Agent 任务工具：[tools.py](E:/agant/-RAG-Database/agent/src/agent/tools.py:31)
- Agent 结果校验：[services.py](E:/agant/-RAG-Database/agent/src/agent/services.py:118)
- Agent 入库与知识库：[ingestion.py](E:/agant/-RAG-Database/agent/src/agent/ingestion.py:12)、[knowledge.py](E:/agant/-RAG-Database/agent/src/agent/knowledge.py:16)
