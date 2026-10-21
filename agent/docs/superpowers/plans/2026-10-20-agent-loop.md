# LangChain 多轮工具循环实现

目标：使用现有 LangChain create_agent / LangGraph 实现模型→工具→模型循环，模型自主补查与筛选后生成回答。

- [x] 工具取消 return_direct；新增 retrieve_classroom、web_search、filter_evidence，证据按请求保存且来源不可由模型伪造。
- [x] before_model 控制每请求轮数；after_model 验证模型最终答案，错误反馈模型继续修正，超限安全结束。
- [x] 流式只解析主模型最终 JSON 的 answer 增量，中间工具结果不作为答案；最终 replace/done 与同步结果一致。
- [x] 其他结构化任务支持模型检查工具结果后明确选用，不允许凭空报告成功。
- [x] 更新 Django 工具白名单、提示词、测试，保留 Tavily 配置、身份授权、通用问答及 session_ref。
- [x] 回归：多次改写检索、筛选删除来源、伪造引用修正、循环上限、会话证据隔离、同步/SSE/数据库最终一致。

保持课堂优先，外部内容仅作证据；不引入新框架。证据校验覆盖来源和引用格式，不能保证所有语义事实正确。每次请求仍独立于 Django 历史记录。

验证结果：85 项 Agent 测试、22 项 Django 集成测试、4 项前端 SSE 测试通过；真实模型运行 3 轮（检索→筛选→生成），返回 5 个答案增量，来源校验通过。
