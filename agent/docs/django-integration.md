# Django 问答接入

2026-10-20：已根据覆盖后的后端恢复接入，兼容无纪要通用问答和 `session_ref`。
桥接配置基于 `Settings.from_env()`，读取后端根目录统一 `.env` 中的 Tavily Key；
Django 已配置的模型 Key 优先。会话记录恢复不等于模型多轮记忆。

实现位于所属后端 `../mysite/qa/agent_bridge.py`，
安装和操作说明见后端根目录 `AGENT_INTEGRATION.md`。

后端的同步、SSE 问答接口现在可在进程内调用当前 Agent。它把经过认证的用户和
Note 绑定为 UserContext，通过 DjangoNoteBackend 读取原有 NoteChunk 向量，
LangGraph 在主模型与课堂检索/联网搜索/证据筛选工具之间循环，主模型生成最终答案，
校验通过后把回答、引用和追问保存为 QaRecord。
前端处理 replace 事件及最终 answer，避免展示与落库内容不同。

问答入口使用 `build_agent(..., tool_names=QA_TOOLS, max_model_calls=8)`，
`QA_TOOLS` 为 retrieve_classroom、web_search、filter_evidence。模型可多轮调用，
最终输出 answer/followups JSON；无效引用反馈模型修正，超限安全结束。
默认调用仍开放原来的完整工具集；空白或未知工具白名单拒绝启动。
这两个参数只由可信宿主设置，不接受 HTTP 用户输入。

此次接入范围为问答。上传、ASR、文档抽取、纪要生成、向量化继续使用后端已有链路；
题库、独立建议和偏好接口未改造。每请求一个 Agent，无跨请求会话记忆。

开发时先在 `.integration-stage` 中验证后端/前端改动。
`scripts/stage_integration.py` 的 apply 命令会检查所有选中文件的原始哈希后才写回同级项目；
正常运行项目不需要这个开发辅助脚本或暂存目录。
