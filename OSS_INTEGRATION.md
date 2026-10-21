# 私有 OSS 文件存储

新材料、头像和反馈截图通过原有 multipart API 上传到 Django，再由 Django 写入私有 OSS。
前端接口保持兼容；这版没有前端直传，上传流量仍经过后端。
数据库只保存对象名，读取 API 时生成默认 15 分钟有效的 HTTPS 签名 URL。
链接过期后重新获取详情或刷新页面以获取新链接，不应持久保存签名 URL。

## 配置并启用

在后端根目录 `E:\agant\-RAG-Database\.env` 配置，**不是 Agent 的 .env**。
程序自动加载此文件，操作系统环境变量优先。已有 .env 时合并变量，不要覆盖其他配置。

```dotenv
RAG_MEDIA_STORAGE=oss
OSS_ENDPOINT=https://oss-cn-beijing.aliyuncs.com
OSS_BUCKET=tmp-gg001
OSS_ACCESS_KEY_ID=新建的RAM用户AccessKey
OSS_ACCESS_KEY_SECRET=新建的RAM用户AccessKeySecret
OSS_PREFIX=classroom
OSS_URL_EXPIRE_SECONDS=900
OSS_CONNECT_TIMEOUT=30
```

将桶设为私有，启用阻止公共访问。新对象会显式设置 private ACL，UUID 文件名避免重名覆盖。
此前在聊天中公开的密钥应禁用并更换，不要把密钥放前端、提交 Git 或粘贴到聊天。
桶必须已存在且位于北京；此接入不会创建桶或修改桶权限。
该后端 RAM 身份只需目标桶 `classroom/*` 对象的 GetObject、PutObject、DeleteObject 权限；
不要授予整个阿里云账号的管理员权限。签名下载也使用该身份的 GetObject 权限。
如使用 STS，设置配套的 `OSS_SECURITY_TOKEN`，并由部署系统在到期前更新整组凭据和重启进程。

```powershell
cd E:\agant\-RAG-Database
# 使用专用增量依赖，避免顺带升级其他后端依赖；完整部署 requirements.txt 也包含这两项。
.\.venv\Scripts\python.exe -m pip install -r requirements-oss.txt
.\.venv\Scripts\python.exe mysite/manage.py check
.\.venv\Scripts\python.exe mysite/manage.py runserver 127.0.0.1:8080
```

生产部署安装完整 requirements.txt，并使用既有 WSGI 服务重启方式；runserver 仅用于本地验证。
`RAG_MEDIA_STORAGE=local` 保持原本地存储；切换为 oss 后缺少必要配置会报错，不会悄悄写回本地。
上线时使用固定且保密的 Django SECRET_KEY，以保护旧文件下载签名。

## 处理链路

- 文档/PDF/PPT：OSS → 临时文件 → 既有解析器 → 纪要与向量化；无论解析成功还是失败均清理临时文件。
- 图片：OSS → 临时文件 → 原有压缩/Base64 视觉识别。
- 音频/视频：OSS → 临时文件 → DashScope 临时 OSS → 原有 ASR；保留既有异步转写协议。
- 原始文件以 OSS 为持久存储；不以节省模型 Token 为目标。
- 删除纪要会删除对应 OSS 对象；上传成功但数据库写入失败、管理员直接删数据库记录等情况可能留下孤立对象，需定期按数据库引用巡检。不要对仍被引用的整个前缀设置短期自动删除。

## 已有本地文件迁移

新对象名带 `oss/` 前缀，旧数据库记录仍按本地文件读取。启用 OSS 后关闭 Django 的公开 `/media/` 路由，
旧文件 URL 改为 `/api/media/legacy/?token=...`，签名过期或篡改返回 404。
代理服务器也必须取消任何直接公开 `media/` 的 Nginx alias 或静态目录映射。

先备份数据库和 media 目录，在维护窗口暂停上传/修改/删除，然后执行：

```powershell
# 默认只检查已被材料、头像、反馈截图引用的本地文件，不写 OSS 或数据库。
.\.venv\Scripts\python.exe mysite/manage.py migrate_media_to_oss
# 启用 OSS 后显式执行：复制、核对大小，再原子更新该文件的所有引用。
.\.venv\Scripts\python.exe mysite/manage.py migrate_media_to_oss --apply
```

命令保留本地原件，可重复运行；已迁移的引用会被跳过。迁移中断可留下未引用对象，恢复后可巡检清理。
如果存在丢失的本地文件，命令在上传前停止；先恢复文件或处理失效记录。
新增 OSS 数据后不要直接切回 local 模式，否则本地存储无法读取 `oss/` 记录。
确认全部迁移完成后可移走旧 media 备份；解析仍需要可写临时目录和足够磁盘空间。

## 部署与验证

由于仍走后端上传，反向代理 body 大小和超时需覆盖产品允许的文件大小（当前视频最高 1GB），
WSGI 超时也应覆盖上传时长。文件上传内存阈值已改为 5MB，大文件由 Django 使用临时磁盘文件。
不需要前端 OSS 上传 CORS；若浏览器需用 fetch/canvas 读取 OSS 内容，再按实际前端域名配置 GET/HEAD CORS。
私有桶签名链接是限时持有者凭证，不写入日志或长期缓存。

```powershell
# 离线测试：SQLite + 模拟 OSS 传输 + SDK 本地签名，不访问生产数据库、云存储或模型。
.\.venv\Scripts\python.exe mysite/manage.py test notes.test_oss notes.test_oss_storage qa.test_agent_integration --settings=mysite.oss_test_settings
```

真实验证：配置新密钥后登录，上传一份无敏感内容的 txt，确认返回 URL 为桶的 HTTPS 签名地址，
桶内 `classroom/oss/notes/sources/` 出现对象；检查纪要解析完成，再删除纪要确认对象删除。
离线测试不能证明云端密钥权限、桶地域、网络及模型服务可用性。
