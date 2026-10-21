# 智学纪要 Agent 接入

现有 `POST /api/qa/ask/` 和 `POST /api/qa/ask/stream/` 已增加 Agent 路径：

2026-10-20 恢复接入：保留覆盖后版本的无纪要通用问答、nullable note 和
`session_ref` 保存/历史查询。没有已完成纪要时由 Agent 使用 Tavily 联网证据回答。

`JWT 用户 → 纪要权限校验 → LangChain 主模型 ↔ 检索/搜索/筛选工具 → 模型最终回答 → 引用校验 → QaRecord`

引用校验失败会回到主模型补查或修正。所有工具结果返回模型，不再单次工具调用后直接退出。

## 安装和启动

Python 3.11+。请在**启动 Django 的同一个虚拟环境**中安装后端与内置 Agent 的依赖。
以下命令从后端仓库根目录（`E:\agant\-RAG-Database`）执行：

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-agent.txt
$env:AGENT_ENABLED = '1'
# 使用后端现有 DASHSCOPE_API_KEY 配置；不要把真实 key 写入代码或提交。
python mysite/manage.py runserver 127.0.0.1:8080
```

`requirements-agent.txt` 依赖后端内部的 `./agent`。根 requirements.txt 已包含该文件，
安装后端依赖会同时安装 Agent。部署包也包含 agent/，服务器不再需要同级 Agent 仓库。
不需要额外启动 FastAPI 或 Agent HTTP 服务。没有数据库模型变动，无新增 migration。

默认启用 Agent。未安装 Agent 包或未配置 key 时，保持旧 RAG/演示检索路径；
显式回退可设置 `AGENT_ENABLED=0` 并重启 Django。
响应头 `X-QA-Engine: agent|legacy` 可用于确认实际选中的路径；
`agent` 表示调用了 Agent 路径，模型故障仍可能返回安全降级答案。

## 数据和协议

- JWT、用户、Note、NoteChunk、QaRecord 和原有接口字段保持兼容。
- 明确指定的纪要不存在或无权限时返回 404，未处理完返回 400；不会偷偷切换到另一篇。
- 用户和课堂身份由服务端设置；工具执行时再次查询用户状态、纪要权限。
- SSE 为 `status / delta / replace / done`。`delta` 是临时内容，`replace.text` 必须覆盖整个缓冲；
  `done` 仍是完整的 QaRecord（含 id、answer、references、followups）。仅最终答案入库一次。
- 前端需要同步应用 `src/api/index.ts` 和 `src/views/QAPage.vue` 的 replace 兼容改动。
- Agent 从后端已有 NoteChunk 读取向量，复用 `mysite.rag.retrieve_scored` 和现有 embedding 配置；
  缺少向量时沿用旧后端的懒索引。不会把生产数据复制到 Agent SQLite。
- 每个请求创建并关闭自己的 Agent；工具线程数据库连接会清理。请求中止时关闭生成器和 HTTP 客户端。
- 当前后端为单租户，`AGENT_TENANT_ID` 仅为部署隔离标识，不代表已经实现多租户权限系统。

## 当前范围

此次接入问答与问答内追问生成。问答端点开放 `retrieve_classroom`、`web_search`、
`filter_evidence`。主模型逐轮审阅工具结果，改写查询或补查，筛选完证据后生成带引用答案。
默认最多 8 轮主模型调用（Django `AGENT_MAX_MODEL_CALLS` 环境变量可设置 2~20）。
工具内部资料审核可能另调模型，轮数不等于所有 HTTP 请求次数。超限/中断返回安全说明。
上传、ASR、PDF/Word/PPT 抽取、纪要生成与向量入库继续由现有后端处理；
题库、独立推荐接口、学习偏好暂未切换到 Agent。
`session_ref` 用于保存与恢复前端会话历史；目前不将历史重新传给模型，
也不提供跨请求 Agent 记忆。历史仍保存在 QaRecord。

Agent 配置通过 `Settings.from_env()` 读取后端根目录统一的 `.env`，系统环境变量优先。
默认搜索为 Tavily，读取 `TAVILY_API_KEY`；`AGENT_SEARCH_PROVIDER=dashscope` 可显式回退。
桥接层保留完整搜索配置，同时优先使用 Django 已配置的 `DASHSCOPE_API_KEY`；
Django 与 Agent 默认使用统一 `.env` 中的模型 Key。
可选环境变量：`AGENT_CHAT_MODEL`、`AGENT_FAST_MODEL`、`AGENT_TIMEOUT`、
`AGENT_ENABLE_SEARCH`、`DASHSCOPE_BASE_URL`、`AGENT_TENANT_ID`。
原后端上传/向量化仍使用 Django 的模型配置；模型密钥与 Agent 从统一 `.env` 加载。

## 离线验证

在已安装后端和 Agent 的同一环境中：

```powershell
python mysite/manage.py test qa.test_agent_integration --settings=mysite.agent_test_settings --noinput
```

使用隔离 SQLite 测试库，不读取 local_settings、不连接生产数据库、不调用付费模型。
测试执行真实 Django ORM、接口和 LangGraph，模型/embedding 使用测试替身。
前端仓库可运行 `node --test tests/streamAsk.test.mjs` 与 `npm run build`。
