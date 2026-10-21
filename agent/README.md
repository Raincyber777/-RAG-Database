# 智学纪要 Agent

上级目录的 Django 后端现已提供同步/流式问答接入，复用现有用户权限、NoteChunk 与 QaRecord。
安装、启用、回退和当前范围见 [Django 接入说明](docs/django-integration.md)。

根据《智学纪要 · 提示词汇总》（2026-10-19）完善的 LangChain / LangGraph 课堂助教。现支持材料抽取/ASR → 分块 → 向量化 → 持久化检索 → 流式课堂问答，可通过 Python API 或 SSE 路由接入现有后端。

## 安装与离线检查

```powershell
uv sync
uv run agent --list-tools
uv run agent --demo
uv run python -m unittest discover -s tests -v
```

`--demo` 使用内置课堂文字和无密钥规则降级，不调用模型、不连接数据库。`--list-tools` 显示八个基础工具；启用知识库入库能力后会增加第九个工具 `ingest_material`。Python 版本要求 ≥3.11。

## 从文件到流式问答

在后端根目录 `../.env` 中填写百炼密钥后执行（以下两个命令会调用真实模型）：

```powershell
uv run agent ingest --file "E:\课程\讲义.pdf" --title "微积分" --course "数学" --tenant-id t1 --user-id u1 --note-id n1
uv run agent ask --question "导数是什么？" --stream --tenant-id t1 --user-id u1 --note-id n1
```

默认保存到 `data/knowledge.db`；可以用 `--db` 指定其他位置。问答每行输出一个 JSON 事件，包含增量文本及最终结果。CLI 的用户/租户参数适用于本地可信操作，不能直接作为线上认证方式。CLI 每次运行的对话历史是新的，知识库材料会持久化。

录音采用百炼异步文件转写，需要一个百炼可访问的 HTTPS URL（如宿主上传后签发的 OSS GET 地址）：

```powershell
uv run agent ingest --audio-url "https://你的存储地址/lecture.wav" --title "课堂录音" --tenant-id t1 --user-id u1 --note-id n1
```

流程为提交 `paraformer-v2` 任务、轮询、读取句子及 `begin_time`、分块、嵌入、入库。默认最多等待约 300 秒/150 次检查（单次网络请求另有超时）；失败不写入不完整材料。文件上传继续使用现有宿主/OSS 流程，签名地址有效期须覆盖整个转写过程。转写限定声道 0，不能保证识别其他声道独有的内容。

## 支持的材料格式

| 格式 | 抽取方式与来源 |
|---|---|
| TXT / Markdown | UTF-8 或 GB18030；逐行来源，保留真实 `[mm:ss]` 时间戳 |
| DOCX | 按顺序读取段落、表格；嵌入图片走 OCR，保留块号/图片编号 |
| PDF | 逐页文字层；无文字层的页面渲染后 OCR，保留页码 |
| PPTX | 幻灯片文本、表格、备注与图片 OCR，保留幻灯片页号 |
| DOC / PPT | 调用本机 LibreOffice 转为新格式；需 `soffice` 或 `libreoffice` 在 PATH 中 |
| PNG / JPG / WebP | 通过视觉模型识别文字 |
| 录音 HTTPS URL | `paraformer-v2`；保留句子起始时间戳 |

默认文件大小上限 50MB，PDF/PPT 上限 300 页。OCR 需要视觉模型密钥；识别失败会阻止整份材料入库，不静默丢弃页面。旧 DOC/PPT 在没有 LibreOffice 时会明确报错，可手动另存为 DOCX/PPTX。文档复杂公式、图表、矢量图及 PDF 混合页面的图片不保证完整还原；当前 PDF 仅对无文字层页面做 OCR，PPTX 图像按图片对象处理。

## 内置知识库与入库工具

```python
from dotenv import load_dotenv
from agent.extraction import Extractor
from agent.gateway import DashScopeGateway
from agent.ingestion import Ingestor
from agent.knowledge import KnowledgeBase
from agent.media import Embedder, SpeechTranscriber
from agent.runtime import open_agent
from agent.schemas import UserContext

load_dotenv(override=False)
gateway = DashScopeGateway()
try:
    kb = KnowledgeBase("data/knowledge.db", Embedder(gateway))
    # 线上必须从已认证会话和已授权纪要生成 context。
    context = UserContext(user_id="u1", tenant_id="t1", note_id="n1")
    kb.register_note(context, title="微积分", course="数学")
    # 由宿主上传处理器登记可信文件路径，模型只能拿到 asset_id。
    asset_id = kb.register_file(context, "E:/课程/讲义.pdf")
    # 录音可改用 kb.register_audio(context, signed_audio_url, name="课堂录音")。
    ingestor = Ingestor(kb, Extractor(ocr=gateway.vision_bytes), SpeechTranscriber(gateway))
    for progress in ingestor.ingest(asset_id, context):
        print(progress)  # extracting / transcribing / embedding / indexed

    # 内置 KnowledgeBase 会自动启用 ingest_material 工具，也可显式注入 ingestor。
    with open_agent(kb, gateway=gateway, ingestor=ingestor) as assistant:
        for event in assistant.stream("导数是什么？", context=context, thread_id="chat-1"):
            print(event)
finally:
    gateway.close()
```

也可让 Agent 调度入库：`assistant.stream(f"导入材料 {asset_id}", context=context, thread_id="chat-1")`。只有最终 `stage=indexed` 才表示成功；入库本身以进度事件反馈，不生成虚假的逐字正文。

向量模型默认 `text-embedding-v4`、1024 维，每批最多 10 条；分块默认 800 字、重叠 100 字，保留来源。SQLite 存储原始片段和检索分块，按租户/用户/纪要隔离，使用真实余弦相似度精确召回。小规模部署无需额外向量服务；大规模时可替换 `Backend.retrieve` 为向量数据库实现。

同一登记资产再次导入时事务替换旧版，抽取/ASR/向量失败保留旧版；不同登记资产会作为不同来源并存。不同签名 URL 被视作不同资产。修改嵌入模型或维度需要新建索引并重新导入，不能混用旧向量。内置知识库默认按资料所有者隔离，不自动提供班级共享或用户资料表。

## 流式事件与前端约定

`StudyAgent.stream()` 使用 LangChain `create_agent` 的模型→工具→模型循环：工具结果返回主模型，模型可改写查询、补查和筛选，再生成最终回答。所有工具均不设置 `return_direct=True`。主模型最终 JSON 中的 answer 字段由流式解析器逐段解码发送，工具参数、中间推理和 JSON 控制字段不作为正文。`StudyAgent.invoke()` 返回同一套校验后的结果。纪要/题库等结构化任务先由模型审阅工具结果，再用 `{"task_result":true}` 选用最近一次真实任务结果，不逐字输出半个 JSON。

| 事件 | 客户端行为 |
|---|---|
| `status` | 展示路由、检索、生成、转写、嵌入等进度 |
| `delta` | 追加 `data.text`；`provisional=true`，尚未完成引用校验 |
| `replace` | **用 `data.text` 替换整个正文**；不能追加。Agent 的最终替换还包含完整 `data.output` |
| `sources` | 更新引用来源列表 |
| `followups` | 更新追问列表 |
| `done` | 本轮结束；`data.output` 为权威结果，另有 `model_call_count` |

完成后统一校验引用、补外部标记和真实来源，再发 `replace`。无效引用会反馈主模型重试，下一轮先清空无效草稿；超过轮次上限或流式中断时用安全的“暂时无法确认”替换，最终只保存 done。临时正文可能包含尚未验证的内容，前端必须实现替换。底层固定流程 `StudyService.answer_stream()` 仍可单独调用，其 `done.data` 是业务结果；外层 Agent 包装为 `output/model_call_count`。

默认每次用户请求最多 8 轮主模型调用（`build_agent(max_model_calls=8)`，范围 2~20），计入格式/引用修正。每轮最多一个工具调用；工具中的资料审核可能另有模型请求，8 轮不是总 HTTP 次数上限。`model_call_count` 是该会话累计主模型调用数。证据池和筛选结果每个请求重置，旧会话消息不能直接充当本次来源。

FastAPI 宿主可以挂载已实现的 SSE 适配器：

```python
from agent.transport import create_router

def mount_agent(app, assistant, authenticated_context_dependency):
    # dependency 必须认证当前请求，验证纪要访问权，返回 UserContext。
    app.include_router(create_router(assistant, authenticated_context_dependency))
```

`POST /agent/stream` 接受 `{"message":"导数是什么？","thread_id":"chat-1"}`，返回 `text/event-stream`。身份和纪要来自认证依赖，不接受请求 JSON 自报 `user_id/tenant_id`。同一会话请求需宿主串行化；上传接口和鉴权系统由宿主提供。

前端使用 `fetch` 读取 POST 流，按 SSE 帧解析，处理示例：

```javascript
// event 是按空行分帧并解析 data JSON 后的 { event, data }。
function onAgentEvent(event) {
  if (event.event === "delta") answerText += event.data.text;
  if (event.event === "replace") answerText = event.data.text;
  if (event.event === "sources") sources = event.data.sources;
  if (event.event === "followups") followups = event.data.followups;
  if (event.event === "done") finalResult = event.data.output;
}
```

将正文以文本或经过清洗的 Markdown 渲染，避免把材料文字直接当 HTML。路由关闭代理缓冲，并将同步模型/SQL 操作移到工作线程；已开始的同步 HTTP 请求在断开后仍可能等待至请求超时，ASR 云任务也不保证自动取消。

## 工具

| 工具 | 功能 |
|---|---|
| `retrieve_classroom` | 查询当前授权课堂，返回相似度 ≥0.22 的最多 4 条候选；可换 query 多次检索 |
| `web_search` | 默认 Tavily 联网补查，来源白名单、低可信度过滤后返回候选；可多次调用 |
| `filter_evidence` | 主模型审阅后选择已返回的证据编号，剔除无关/重复内容；新检索后须重新筛选 |
| `answer_question` | 兼容旧固定流程的问答草稿工具；结果也回到主模型审阅。新问答入口使用上面三个工具 |
| `summarize_notes` | 主题、知识点、术语、重点；超过 15000 字按 ≤12000 字分段提取再融合 |
| `generate_quiz` | 单选、多选、不定项、判断、填空、简答；1~20 道；逐题校验，返回不足数量 |
| `suggest_followups` | 参考当前材料与最多六条历史提问，用 qwen-flash 生成最多四条建议 |
| `transcribe_image` | qwen3-vl-plus 转写板书/PPT，接受 HTTPS 图片地址 |
| `get_current_user_profile` | 读取当前已认证用户的资料 |
| `get_user_preferences` | 读取当前用户学习偏好 |
| `save_user_preference` | 用户明确要求时保存学习偏好，后续回答自动读取 |
| `ingest_material` | 启用入库器时提供；通过已授权 asset_id 执行 ASR/抽取、分块、嵌入及持久化 |

偏好键为 `language`、`explanation_style`、`difficulty`、`learning_goal`，值为 1~200 字；偏好只影响表达，不覆盖中文输出和引用要求。

## 接入已有后端

实现 [Backend 协议](src/agent/backend.py)，向 `build_agent` 或 `open_agent` 传入实例：

```python
from dotenv import load_dotenv
from agent.runtime import open_agent
from agent.schemas import UserContext

load_dotenv(override=False)  # 仅在宿主启动时显式加载，不覆盖系统环境变量。

# backend 是宿主实现的 Backend，见下方三个接口。
def ask(backend, authenticated_user, note_id, conversation_id, question):
    context = UserContext(
        user_id=str(authenticated_user.id),
        tenant_id=str(authenticated_user.tenant_id),
        note_id=str(note_id),
    )
    with open_agent(backend) as assistant:
        return assistant.invoke(question, context=context, thread_id=conversation_id)
```

上例演示单次调用；多轮对话应在应用启动时创建并复用 `assistant`，在关闭时调用 `close()`，或使用持久化检查点。不要每次请求新建内存 Agent，否则历史不会保留。同一会话的并发调用应由宿主串行化。

后端需提供以下方法，所有方法都应依据 `UserContext` 检查租户、用户及纪要访问权限；无权访问抛出 `PermissionError`：

```python
material(context: UserContext) -> Material
retrieve(question: str, context: UserContext, limit: int = 4) -> list[Chunk]
profile(context: UserContext) -> dict | None
```

- `Material(title, course, text)`：已抽取的课堂材料；录音行首使用 `[08:15]` 等真实时间戳，出题来源标签可在正文中写作 `（录音 08:15）`。
- `Chunk(text, ref, score)`：已授权片段、来源标签与**真实余弦相似度**。示例 `ref="录音 08:15"` 可用于前端定位。不能将距离或重排分数直接作为余弦分数。可使用内置 `KnowledgeBase`，或接入宿主已有的检索实现。
- `profile`：当前用户资料；工具只返回 `user_id/tenant_id/name/email/role` 白名单字段。
- 不传后端时使用 `EmptyBackend`：无课堂材料、无用户资料。配置了模型和搜索后仍可回答外部问题，但不会伪造课堂内容。

`UserContext` 必须由已认证的宿主构建，不能直接信任请求体里的身份。会话键包含租户、用户、纪要和会话 ID 的哈希，避免重名会话串读。

返回格式：

```json
{"output": {"answer": "回答", "followups": [], "sources": [], "mode": "kb", "degraded": false}, "model_call_count": 1}
```

`output` 随工具变化：纪要返回主题/知识点等，题库返回 `questions/missing_count`。`model_call_count` 自动累计当前会话的**调度模型尝试次数**，不包括工具内各业务模型调用；运行故障时为 `null`。工具结果直接返回，不经调度模型二次加工；调度模型绕过工具的自由回答不会作为课程答案返回。

## 配置与数据库

参考 [统一配置模板](../.env.example)，在本地配置 `DASHSCOPE_API_KEY` 和 `TAVILY_API_KEY`。默认主模型 `qwen-plus`、追问模型 `qwen-flash`、图片模型 `qwen3-vl-plus`；可以通过 `AGENT_CHAT_MODEL` 等变量替换。`AGENT_ENABLE_SEARCH=false` 关闭联网。

常规生成使用百炼 OpenAI 兼容接口；联网搜索默认使用 Tavily Search API（`AGENT_SEARCH_PROVIDER=tavily`），通过现有 httpx 调用，无需额外 SDK。每次 basic 搜索最多取 5 条结果，保留逐条网页摘要与真实 URL，再由现有资料审核和引用校验流程处理。课堂证据充分时不会联网。搜索失败或缺少 Tavily Key 时返回无搜索证据，不会悄悄切换服务商；需要回退时设置 `AGENT_SEARCH_PROVIDER=dashscope`，使用原有百炼搜索。HTTP 超时、调用失败、无效 JSON 不自动重试。

`Settings.from_env()` 在内置源码运行时读取后端根目录的 `.env`（独立源码项目仍读取其自身 `.env`），系统环境变量优先，且不修改进程环境；也可传入 `Settings.from_env(env_file=绝对路径)`。安装 wheel 后使用系统环境变量或显式文件路径。直接构造 `Settings(...)` 不读取 `.env`，宿主若需要覆盖模型配置，推荐 `dataclasses.replace(Settings.from_env(), api_key=宿主模型密钥)`，以保留 Tavily 配置。两种 Key 都不会出现在 Settings 的 repr 中。

默认检查点和偏好均在内存中，进程退出即丢失。若需要 PostgreSQL 会话持久化：

```python
import os
from agent.runtime import open_agent

# 应用启动阶段进入上下文，退出时连接关闭。
with open_agent(backend, postgres_uri=os.environ["POSTGRES_URI"]) as assistant:
    result = assistant.invoke(question, context=context, thread_id=conversation_id)
```

数据库检查点表首次创建需显式传入 `initialize_schema=True`。导入模块不连接数据库、不执行建表；本次实现没有运行任何数据库迁移。PostgreSQL 检查点不自动持久化用户偏好，如有需要通过 `store=` 注入宿主的持久化 LangGraph Store。

## 校验与降级

- 问答：无课堂/可靠外部证据时直接返回固定话术，不调用回答生成模型；外部失败但课堂有相关片段时退回课堂档。
- 引用：未知 `[编号]` / `[E编号]`、模型自写 URL、未引用的结论句会触发摘录降级；真实来源标题/URL由代码拼装，外部引用自动补「补充自外部知识」。校验聚焦中文句式，不能证明自然语言结论与材料语义完全一致。
- 纪要：时间戳只能来自输入材料真实行首；错误时间戳删除。模型失败返回标记为降级的原文摘录；长文融合失败保留合并的分段结果。
- 出题：验证六种题型、四个不重复选项、字母答案、判断答案、填空标记、来源引用及题干去重；不合格题丢弃，`missing_count` 告知缺口。单次出题使用材料前 15000 字，超出时标记 `material_truncated=true`。
- 追问：去重、过滤历史问题、限制长度；不足两条个性化建议时返回空列表。`StudyService.followups(question, answer)` 可为宿主已有的回答生成最多三条追问。
- 图像：无凭据或视觉失败返回空文本与降级原因；不猜测图片内容。
- 材料和搜索内容被明确标识为数据，不当作操作指令；格式校验不能替代真实课堂数据上的效果评测。

如不需要模型调度，可直接使用 `StudyService(backend, gateway)` 调用 `answer/answer_stream/summarize/quiz/suggest/followups/transcribe_image`。模型生成不可用时提供规则降级；依赖远程嵌入的检索故障会通过外层 Agent 返回服务不可用结果，不以伪造相似度替代。

## 目录

```text
src/agent/
  prompts.py    # 调度与各业务提示词
  schemas.py    # 上下文、证据和严格输出模型
  backend.py    # 宿主后端接口
  gateway.py    # 百炼模型、Tavily 搜索与可选百炼搜索适配器
  services.py   # 分档、生成、输出校验和降级
  tools.py      # 八个基础工具 + 可选材料入库工具
  runtime.py    # 工厂、会话隔离、自动计数、可选 PostgreSQL
  transport.py  # SSE 事件编码与认证依赖注入的 FastAPI 路由
  extraction.py # 文档/PDF/PPT 抽取和图片 OCR
  media.py      # ASR 任务与向量化接口
  materials.py  # 带来源的片段与分块
  knowledge.py  # SQLite 持久化材料/向量与余弦检索
  ingestion.py  # 已登记材料的完整入库链路
  cli.py        # 离线示例、入库、流式问答
  1/agent.py    # 旧路径兼容导出，无导入副作用
tests/          # 不连接外部服务的单元与 Agent 图集成测试
```

现有 `oss.py` 上传签名接口未修改。ASR、向量化、材料处理和 SSE 适配已实现；OSS 上传、生产认证及前端页面由宿主接入。

## 目录与启动环境

Agent 已内置到后端 agent/。统一环境、配置路径与迁移说明见 [迁移说明](../AGENT_MIGRATION.md)。
