"""One-time, guarded relocation into the backend; preserves local secrets and history."""
import hashlib
import json
from pathlib import Path
import shutil


def main():
    source = Path('E:/agant/agent').resolve()
    backend = Path('E:/agant/-RAG-Database').resolve()
    target = backend / 'agent'
    if source != Path(__file__).resolve().parents[1] or target.exists():
        raise SystemExit('Unexpected source or existing target; refusing migration')
    if not (backend / 'mysite/manage.py').is_file():
        raise SystemExit('Backend not found')
    if not source.is_relative_to(Path('E:/agant')) or not target.is_relative_to(backend):
        raise SystemExit('Paths are outside the requested project directories')
    # Verify the credentials and source files survived without showing their contents.
    protected = [p for p in source.rglob('*') if p.is_file() and
                 (p == source / '.env' or p.is_relative_to(source / 'src') or p.is_relative_to(source / 'tests'))]
    digests = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    shutil.move(str(source), str(target))
    for name, expected in digests.items():
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != expected:
            raise SystemExit('Relocated file verification failed: ' + name)
    # Keep a recoverable copy of the old repository metadata without making a nested repo.
    if (target / '.git').exists():
        (target / '.git').rename(target / '.git-before-move')
    # Windows virtualenv launchers contain absolute paths. Use the backend venv instead.
    if (target / '.venv').exists():
        (target / '.venv').rename(target / '.venv-before-move')

    def edit(path, old, new):
        content = path.read_text(encoding='utf-8')
        if old not in content:
            raise SystemExit('Expected text not found in ' + str(path))
        path.write_text(content.replace(old, new), encoding='utf-8')

    (backend / 'requirements-agent.txt').write_text(
        '# Run from the backend repository root; bundled local Agent source.\n-e ./agent\n', encoding='utf-8')
    with (backend / 'requirements.txt').open('a', encoding='utf-8') as file:
        file.write('\n# Bundled Agent runtime\n-r requirements-agent.txt\n')
    with (backend / '.gitignore').open('a', encoding='utf-8') as file:
        file.write('\n# Preserved pre-migration local metadata and environment\n'
                   'agent/.git-before-move/\nagent/.venv-before-move/\n')
    with (target / '.gitignore').open('a', encoding='utf-8') as file:
        file.write('\n.git-before-move/\n.venv-before-move/\n')
    edit(target / 'scripts/stage_integration.py',
         "REPOS = {'backend': ROOT.parent / '-RAG-Database', 'frontend': ROOT.parent / 'RAG-AI-system'}",
         "REPOS = {'backend': ROOT.parent, 'frontend': ROOT.parent.parent / 'RAG-AI-system'}")
    edit(target / 'docs/django-integration.md',
         '同级后端 `../-RAG-Database/mysite/qa/agent_bridge.py`',
         '所属后端 `../mysite/qa/agent_bridge.py`')
    edit(target / 'README.md', '同级 Django 后端', '上级目录的 Django 后端')
    for path in (list((target / 'docs').rglob('*.md')) + list((target / '.idea').glob('*.xml'))
                 + list((target / '.idea').glob('*.iml'))):
        text = path.read_text(encoding='utf-8')
        changed = text.replace('E:/agant/agent', 'E:/agant/-RAG-Database/agent')
        changed = changed.replace('E:\\agant\\agent\\.venv', 'E:\\agant\\-RAG-Database\\.venv')
        changed = changed.replace('E:\\agant\\agent', 'E:\\agant\\-RAG-Database\\agent')
        if changed != text:
            path.write_text(changed, encoding='utf-8')
    edit(backend / 'AGENT_INTEGRATION.md', '安装两个仓库的依赖', '安装后端与内置 Agent 的依赖')
    edit(backend / 'AGENT_INTEGRATION.md',
         '`requirements-agent.txt` 依赖同级 `../agent` 项目。服务器没有同级源码时，\n先在 Agent 仓库运行 `uv build`，把产生的 wheel 安装进后端环境，然后按原部署方式启动。',
         '`requirements-agent.txt` 依赖后端内部的 `./agent`。根 requirements.txt 已包含该文件，\n安装后端依赖会同时安装 Agent。部署包也包含 agent/，服务器不再需要同级 Agent 仓库。')
    workflows = list((backend / '.github/workflows').glob('*.yml')) + list((backend / '.github/workflows').glob('*.yaml'))
    for workflow in workflows:
        text = workflow.read_text(encoding='utf-8')
        if '-C mysite .' not in text:
            continue
        text = text.replace('-C mysite .', '-C mysite . -C .. agent/src agent/pyproject.toml agent/README.md')
        text = text.replace('./venv/bin/pip install -q -r requirements.txt',
                            './venv/bin/pip install -q -r requirements.txt\n            ./venv/bin/pip install -q ./agent')
        workflow.write_text(text, encoding='utf-8')
    note = '''# Agent 目录迁移（2026-10-21）

目录结构：
```
E:/agant/
  -RAG-Database/
    agent/           # 原 Agent 全目录，源码、工具、测试、.env 均保留
    mysite/          # Django
    .venv/           # 统一运行环境
    .env             # 后端 OSS、邮箱等配置
  RAG-AI-system/     # 前端
```

从后端根目录安装、启动：
```powershell
cd E:\\agant\\-RAG-Database
.\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.\\.venv\\Scripts\\python.exe mysite/manage.py runserver 127.0.0.1:8080
```

Agent 模型和 Tavily 配置位于 `agent/.env`；后端配置仍位于根 `.env`。
两份文件保留原内容，Agent 的路径定位不依赖运行目录。进程环境变量优先。
前端仍在 `E:/agant/RAG-AI-system` 执行 `npm run dev`。
PyCharm 请重新打开后端目录，解释器选择后端 `.venv/Scripts/python.exe`。

Agent 独立 CLI 和测试使用同一后端解释器：
```powershell
cd E:\\agant\\-RAG-Database\\agent
..\\.venv\\Scripts\\python.exe -m agent --list-tools
..\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v
```

原 `agent/.venv` 已完整保留为 `agent/.venv-before-move`，其 Windows 启动器仍含旧路径，
不要继续用它启动。若确实需要独立 Agent 环境，在 agent/ 运行 uv sync 创建新环境。
原 Agent `.git` 完整保留为 `.git-before-move` 并忽略提交；Agent 源码现在由后端仓库管理。
历史暂存区、构建产物、缓存均随目录保留，不参与正常运行或部署。
没有迁移数据库、删除文件、提交 Git 或触发线上发布。

GitHub Actions 部署包保持原 Django 布局，额外包含 agent 源码并安装该包。
私密 .env 不打包，线上密钥继续由部署环境提供；使用 Tavily 时需在服务器环境中设置 TAVILY_API_KEY。
'''
    (backend / 'AGENT_MIGRATION.md').write_text(note, encoding='utf-8')
    with (target / 'README.md').open('a', encoding='utf-8') as file:
        file.write('\n## 目录与启动环境\n\nAgent 已内置到后端 agent/。统一环境、配置路径与迁移说明见 [迁移说明](../AGENT_MIGRATION.md)。\n')
    print(json.dumps({'destination': str(target), 'verified_source_files': len(digests),
                      'old_directory_exists': source.exists()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
