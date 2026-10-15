# 智学纪要 · RAG 知识问答系统 后端

Django 5 + Django REST Framework + JWT + PostgreSQL。
为「用户端（5173）」与「管理端（5174）」两个 Vue 前端提供接口。

## 目录结构

```
-RAG-Database/
├── requirements.txt        # 依赖清单
└── mysite/
    ├── manage.py
    ├── accounts/           # 账号：统一登录 / 注册 / 资料 + 管理端账号管理
    ├── notes/              # 课堂纪要：上传 / 列表 / 详情 / 删除 / 重新处理
    ├── qa/                 # 智能问答：提问（演示检索）/ 历史 / 点赞
    ├── quiz/               # 题库：生成（演示占位）/ 批改 / 错题本
    ├── feedback/           # 问题反馈：提交（可附截图）/ 管理端处理
    ├── faq/                # 常见问题：两端展示 + 管理端维护
    └── mysite/             # 工程配置（settings / urls）+ 数据速览
```

## 首次启动（数据库迁移步骤）

1. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

2. 在 PostgreSQL 中创建数据库（pgAdmin 或 psql 均可）：

   ```sql
   CREATE DATABASE rag_db ENCODING 'UTF8';
   ```

3. 修改 `mysite/mysite/settings.py` 中 `DATABASES` 的 `NAME / USER / PASSWORD` 为本机 PG 的实际值。

4. 生成并执行迁移：

   ```bash
   cd mysite
   python manage.py makemigrations
   python manage.py migrate
   ```

5. 写入演示数据（两端演示账号 + 示例纪要 / 问答 / 题库 / 反馈 / FAQ）：

   ```bash
   python manage.py seed_demo
   ```

6. 启动开发服务器（默认 8000）：

   ```bash
   python manage.py runserver
   ```

   创建超级用户进 Django 后台（`/admin/dj/`）可选：`python manage.py createsuperuser`

## 演示账号

| 角色 | 账号 | 密码 |
| --- | --- | --- |
| 超级管理员 | admin@zhiyuan.ai | admin123456 |
| 管理员 | chenjw@zhiyuan.ai | admin123456 |
| 普通用户 | linxz@zhiyuan.ai | 123456 |

## 接口一览（均以 `/api` 开头，认证用 `Authorization: Bearer <access>`）

| 方法 | 路径 | 说明 | 权限 |
| --- | --- | --- | --- |
| POST | /api/auth/register/ | 注册（name/phone/email/password） | 公开 |
| POST | /api/auth/login/ | 统一登录（account=邮箱/手机号/用户名） | 公开 |
| POST | /api/auth/logout/ | 登出（body: refresh） | 登录 |
| GET/PATCH | /api/auth/me/ | 我的资料 / 修改资料（支持头像） | 登录 |
| POST | /api/auth/password/ | 修改密码（old_password/new_password） | 登录 |
| GET/POST | /api/notes/ | 我的纪要 / 上传创建（multipart，字段 source） | 登录 |
| GET/DELETE | /api/notes/{id}/ | 纪要详情 / 删除 | 本人或管理员 |
| POST | /api/notes/{id}/reprocess/ | 重新处理 | 本人或管理员 |
| POST | /api/qa/ask/ | 提问（note_id? + question），返回答案与引用 | 登录 |
| GET | /api/qa/ | 我的问答历史 | 登录 |
| POST | /api/qa/{id}/like/ | 点赞 / 取消 | 登录 |
| GET | /api/quiz/ | 题库（course/chapter/type/search） | 登录 |
| POST | /api/quiz/generate/ | 基于纪要出题（note_id, count?） | 登录 |
| POST | /api/quiz/{id}/submit/ | 提交作答并批改，错题进错题本 | 登录 |
| GET | /api/quiz/wrong/ | 我的错题本 | 登录 |
| GET/POST | /api/feedback/ | 我的反馈 / 提交反馈（multipart，截图 images[]） | 登录 |
| GET | /api/faqs/ | 常见问题（scope=user/admin） | 公开 |
| GET | /api/stats/summary/ | 数据速览（按角色返回口径） | 登录 |
| GET | /api/admin/accounts/ | 账号列表（role/status/search） | 管理员 |
| POST | /api/admin/accounts/create/ | 新建管理员 | 超管 |
| POST | /api/admin/accounts/{id}/reset_password/ | 重置密码 | 管理员 |
| POST | /api/admin/accounts/{id}/toggle_status/ | 停用 / 恢复 | 管理员 |
| GET | /api/admin/notes/ | 全部纪要巡检 | 管理员 |
| GET | /api/admin/qa/ | 全部问答巡检 | 管理员 |
| GET | /api/admin/quiz/ | 全部题目巡检 | 管理员 |
| GET | /api/admin/feedbacks/ | 全部反馈（status=） | 管理员 |
| POST | /api/admin/feedbacks/{id}/resolve/ | 标记已处理 | 管理员 |
| GET/POST | /api/admin/faqs/ | FAQ 列表 / 新增 | 管理员 |
| PUT/DELETE | /api/admin/faqs/{id}/ | FAQ 编辑 / 删除 | 管理员 |

分页：`?page=1&page_size=10`（上限 50），返回 `{count, next, previous, results}`。

## 与真实 RAG / Agent 流水线的衔接（TODO）

- 纪要生成：`notes/views.py::generate_stub` —— 替换为异步任务（音频抽取 → 抽帧 → OCR → ASR → LLM 融合生成 → 向量入库），完成后回写各板块并置为 done。
- 智能问答：`qa/views.py::demo_answer` —— 替换为 pgvector 相似度检索 + LLM 生成，引用对齐时间戳 / 页码。
- 练习出题：`quiz/views.py::QuizGenerateView` —— 替换为 LLM 按纪要批量生成各类题型。

接口结构与字段已按两端前端数据模型设计，替换内部实现后前端无需改动。
