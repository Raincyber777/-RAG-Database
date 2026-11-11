# 智学纪要 —— 多模态课堂知识助手 · 服务端

> 把课堂音视频与课件，变成可检索、可追问、可出题、可追溯的学习资产。
> 每一句答案都能指回课堂原话：出处可核验，是本系统的一等功能，而非事后说明。

本仓库为**服务端**（Django）。用户端与 管理端 为两个独立前端仓库：

| 仓库 | 说明 | 技术栈 |
|------|------|--------|
| [RAG-AI-system](https://github.com/Raincyber777/RAG-AI-system) | 用户端：工作台 / 纪要 / 问答 / 练习 / 看板 | Vue 3 + TS + Vite + Pinia + Element Plus |
| [RAG-admin](https://github.com/Raincyber777/RAG-admin) | 管理端：账号 / 巡检 / 审核 / 训练样本 / 公告 | Vue 3 + TS + Vite + Pinia + ECharts |
| **-RAG-Database（本仓库）** | 服务端：解析 / 转写 / 抽取 / 检索问答 / 出题 / 鉴权 | Django 5.2 + DRF + PostgreSQL 17 |

## 一、项目介绍

**智学纪要**是一套面向高校课程团队、教培机构与知识付费创作者的多模态课堂知识助手。它解决的核心问题不是"把录音转成文字"，而是"让转出来的文字能被检索、被追问、被出题，并且每一句都能指回原话"：

- 课堂录音转写产出上万字文本，但复习时仍需从头读到尾——**没有知识点边界**；
- 教师出题、写讲解稿都要回到课堂原话找依据——**没有出处定位**；
- 通用大模型直传音视频输出不稳定、无法定位原话——**不可核验，进不了正式备课流程**。

### 核心链路

```
材料上传（音频/视频/图片/文档/压缩包，支持文件夹批量）
        │
        ▼
材料解析 ──► 语音转写（Paraformer-v2，带时间戳）──► 片段切分（语义 + 时长双约束）
        │                                         板书/PPT 关键帧抽取（ffmpeg + 图像理解）
        ▼
知识点抽取（概念/定义/重点，逐条关联来源片段 + 时间戳/页码）
        │
        ▼
向量入库（text-embedding-v4，1024 维）
        ├──► 课堂纪要（按板块流式生成，带出处）
        ├──► 智能问答（三档检索阈值 + 引用强制校验 + 覆盖不足联网补充）
        └──► 题库生成（六类题型，题目带出处）──► 自创题练习 / 错题本
```

三类产物（纪要、问答、题库）**共用同一份片段出处数据**：同一处知识在三个场景中指向同一句原话，即"一堂课一份资产"。

### 当前运行数据（2026 年 11 月，管理端实测）

- 课堂纪要 **38** 份 ｜ 问答记录 **241** 条 ｜ 题库题目 **34** 道（覆盖 3 门课程）
- 账号 **11** 个 ｜ 管理端 **12** 个板块可用 ｜ 待处理反馈 **0** 条

## 二、技术架构

| 层次 | 选型 | 说明 |
|------|------|------|
| 服务端框架 | Django 5.2 + Django REST Framework + SimpleJWT | REST API + 流式 SSE |
| 关系数据库 | PostgreSQL 17 | 账号、纪要、片段（含 1024 维向量字段）、题目、账号（可审计） |
| 向量检索 | numpy 余弦相似度（片段向量存于 PG） | 无独立向量库；强/弱/无依据三档阈值检索 |
| 对象存储 | 阿里云 OSS（`RAG_MEDIA_STORAGE=oss`，可切本地 media/） | 原始材料与产物，签名 URL 访问 |
| 语音识别 | 阿里云百炼 Paraformer-v2 文件转写 | 长音频分段送转，输出带时间戳 |
| 多模态理解 | 阿里云百炼 qwen3-vl-plus | 板书 / PPT 关键帧图像理解 |
| 语言模型 | 阿里云百炼 Qwen（qwen-plus 主力 / qwen-flash 轻量兜底） | 知识点抽取、问答生成、题库生成 |
| 联网检索 | Tavily（可关闭） | 知识库覆盖不足时补充外部知识并标注来源 |
| 纪要流水线 | 独立 note_worker 进程 | 数据库状态机 + 串行队列，支持排队取消、断点重试 |
| 部署 | 阿里云 ECS + gunicorn + Nginx + GitHub Actions | 三仓库各自 CI：类型检查 → 构建 → 自动部署 |

### 架构分层

```
接入层：用户端 (:8082) / 管理端 (:8081)
   │
服务层：JWT 鉴权 │ 内容安全审核（词库预筛 + 轻量模型复核）│ 纪要流水线（note_worker）
   │      检索问答（流式）│ 引用强制校验 │ 题库生成 │ 反馈闭环（片段权重进化 + 训练样本）
模型层：qwen-plus │ qwen-flash │ qwen3-vl-plus │ paraformer-v2 │ text-embedding-v4（均可配置切换）
   │
存储层：PostgreSQL │ 阿里云 OSS
```

### 降级与可靠性设计

1. **主模型限流** → 自动降级 qwen-flash 轻量模型兜底；
2. **联网检索不可用 / `TAVILY_API_KEY` 留空** → 仅用知识库作答，并在证据不足时明确拒答而非编造；
3. **媒体存储可切换** → `RAG_MEDIA_STORAGE=local|oss` 一行配置切换本地 / 云存储；
4. **生成任务可控** → 排队任务可取消、失败可重新处理，note_worker 串行消费防止并发踩踏。

选型原则：**可替换优先于性能最优**。转写、语言模型与向量模型均通过配置项切换，任一供应商停服可在不修改业务代码的情况下替换。

## 三、快速开始

### 3.1 环境要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 服务端运行时 |
| PostgreSQL | 16+（开发用 17） | 关系数据库（含片段向量） |
| Node.js | ≥ 18 | 前端构建 |

### 3.2 获取代码

```bash
git clone https://github.com/Raincyber777/-RAG-Database.git
cd -RAG-Database
```

### 3.3 配置环境变量

复制模板并填入真实值（**切勿将真实密钥提交进仓库**）：

```bash
cp .env.example .env
# 编辑 .env：数据库口令 + DASHSCOPE_API_KEY 为必填，其余默认值即可跑通
```

完整变量说明见 [docs/环境配置说明.md](docs/环境配置说明.md)。

### 3.4 初始化与启动

```bash
pip install -r requirements.txt

# PostgreSQL 中创建数据库
#   CREATE DATABASE rag_db ENCODING 'UTF8';

cd mysite
python manage.py migrate
python manage.py seed_demo          # 演示账号 + 示例纪要/问答/题库/反馈/FAQ（账号见命令输出）

python manage.py runserver          # API：http://127.0.0.1:8080
python manage.py note_worker        # 纪要生成流水线（另一终端）
```

### 3.5 启动前端

```bash
# 用户端
git clone https://github.com/Raincyber777/RAG-AI-system.git
cd RAG-AI-system && npm install && npm run dev      # http://localhost:5173

# 管理端
git clone https://github.com/Raincyber777/RAG-admin.git
cd RAG-admin && npm install && npm run dev          # http://localhost:5174
```

### 3.6 验证部署

1. 登录用户端，上传一份音频或文档材料，确认材料进入"排队中 → 生成中 → 已完成"状态；
2. 打开纪要详情，确认知识点带时间戳/页码出处并可跳转回听；
3. 在智能问答中提问，确认答案带编号引用；提问一个材料外问题，确认系统拒答而非编造；
4. 在自创题练习中生成题目并作答，确认错题进入错题本；
5. 打开数据看板，确认个人学习数据（课堂/追问/练习/错题）与图表正常。

## 四、目录结构

```
-RAG-Database/
├── README.md
├── .env.example              # 环境变量模板（不含真实密钥）
├── docs/                     # 环境配置说明等文档
├── requirements*.txt         # 依赖清单（主 + agent + oss）
├── agent/                    # Agent 运行时（模型网关 / 工具循环 / 检索）
└── mysite/
    ├── manage.py
    ├── accounts/             # 账号：注册/登录（邮箱验证码）/资料/管理端账号管理
    ├── notes/                # 课堂纪要：上传/列表/详情/重新处理/取消 + note_worker 流水线
    ├── qa/                   # 智能问答：流式提问/会话目录/建议/反馈闭环/审核
    ├── quiz/                 # 题库：生成/批改/错题本
    ├── feedback/             # 问题反馈：提交（可附截图）/管理端处理
    ├── faq/                  # FAQ 与使用技巧：两端展示 + 管理端维护
    ├── announcements/        # 公告：发布/已读/角标提醒
    ├── safety/               # 内容安全：健康档案/禁忌阻断/敏感操作审计
    └── mysite/               # 工程配置（settings/urls）+ 数据速览
```

## 五、核心接口（节选，均以 `/api` 开头，认证用 `Authorization: Bearer <access>`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api/auth/register/ · /api/auth/login/ · /api/auth/logout/ | 注册 / 统一登录（邮箱/手机号/用户名）/ 登出 |
| GET/PATCH | /api/auth/me/ | 我的资料 / 修改资料（支持头像） |
| GET/POST | /api/notes/ | 我的纪要 / 上传创建（multipart，支持批量） |
| GET/DELETE | /api/notes/{id}/ | 纪要详情（含板块与出处）/ 删除 |
| POST | /api/notes/{id}/reprocess/ · /api/notes/{id}/cancel/ | 重新处理 / 取消排队任务 |
| POST | /api/qa/ask/stream/ | 流式提问（SSE：delta/done/blocked 事件） |
| GET | /api/qa/sessions/ · /api/qa/suggestions/ | 会话目录（课内会话归档）/ 个性化追问建议 |
| POST | /api/qa/{id}/feedback/ | 消息反馈（like/dislike/copy/reask/dwell → 片段权重进化） |
| GET/POST | /api/quiz/ · /api/quiz/generate/ · /api/quiz/{id}/submit/ | 题库 / 出题 / 作答批改 |
| GET | /api/quiz/wrong/ | 错题本 |
| GET | /api/stats/summary/ · /api/stats/dashboard/ | 数据速览 / 看板聚合（支持 ?scope=personal 个人口径） |
| GET/POST | /api/feedback/ · /api/faqs/ · /api/tips/ | 反馈 / 常见问题 / 使用技巧 |
| GET/POST | /api/admin/accounts/ · /api/admin/notes/ · /api/admin/qa/ | 管理端：账号 / 纪要巡检 / 问答巡检（支持批量删除） |
| GET/POST | /api/admin/training/generate/ · /api/admin/training/samples/ | 训练样本生成 / 审核池 / JSONL 导出 |
| GET | /api/admin/audit/ | 敏感操作审计日志 |

分页：`?page=1&page_size=10`（上限 50），返回 `{count, next, previous, results}`。

## 六、安全与合规说明

- 所有 API 密钥通过**环境变量**注入，仓库中不含任何真实密钥（仅提供 `.env.example` 模板）；
- 上传材料按**用户归属做行级权限校验**，管理端操作分级授权（管理员 / 超级管理员）；
- 提问经内容安全门禁：高置信违规直接拦截、健康类问题建档并做禁忌冲突阻断、争议问题引导回学习范围，全过程写**脱敏审计日志**；
- AI 生成内容按《人工智能生成合成内容标识办法》添加显式标识，回答末尾按领域追加免责声明；
- 注册 / 换绑使用邮箱验证码，密码 PBKDF2 散列存储。

## 七、团队信息

| 角色 | 姓名 | 职责 |
|------|------|------|
| 队长 / 产品负责人 | 【待补】 | 需求定义、原型与验收标准、迭代评审、对外合作 |
| 后端与模型集成 | 【待补】 | 解析、转写调度、知识点抽取、题库生成、检索与引用校验 |
| 前端与交互 | 【待补】 | 用户端与管理端页面、上传与问答交互、异常态设计 |
| 测试与质量 | 【待补】 | 测试用例、回归清单、引用核对抽样、缺陷跟踪 |
| 运营与内容 | 【待补】 | 种子用户、课程材料采集、数据整理、反馈处理 |

指导教师：【待补】

## 八、开源协议

本项目为参赛作品，采用 MIT License。
