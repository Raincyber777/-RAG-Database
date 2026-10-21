# Agent 目录迁移（2026-10-21）

目录结构：
```
E:/agant/
  -RAG-Database/
    agent/           # 原 Agent 全目录，源码、工具、测试均保留
    mysite/          # Django
    .venv/           # 统一运行环境
    .env             # 后端与 Agent 统一配置
  RAG-AI-system/     # 前端
```

从后端根目录安装、启动：
```powershell
cd E:\agant\-RAG-Database
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mysite/manage.py runserver 127.0.0.1:8080
```

后端、Agent 模型和 Tavily 统一读取后端根 `.env`；模板见根 `.env.example`。
原 agent/.env 已合并并归档为 agent/.env.before-merge（忽略提交、不再读取）。
Agent 路径定位不依赖运行目录，进程环境变量优先。
前端仍在 `E:/agant/RAG-AI-system` 执行 `npm run dev`。
PyCharm 请重新打开后端目录，解释器选择后端 `.venv/Scripts/python.exe`。

Agent 独立 CLI 和测试使用同一后端解释器：
```powershell
cd E:\agant\-RAG-Database\agent
..\.venv\Scripts\python.exe -m agent --list-tools
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

原 `agent/.venv` 已完整保留为 `agent/.venv-before-move`，其 Windows 启动器仍含旧路径，
不要继续用它启动。若确实需要独立 Agent 环境，在 agent/ 运行 uv sync 创建新环境。
原 Agent `.git` 完整保留为 `.git-before-move` 并忽略提交；Agent 源码现在由后端仓库管理。
历史暂存区、构建产物、缓存均随目录保留，不参与正常运行或部署。
没有迁移数据库、删除文件、提交 Git 或触发线上发布。

GitHub Actions 部署包保持原 Django 布局，额外包含 agent 源码并安装该包。
私密 .env 不打包，线上密钥继续由部署环境提供；使用 Tavily 时需在服务器环境中设置 TAVILY_API_KEY。
