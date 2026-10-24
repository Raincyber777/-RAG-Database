# 智能问答图片识别

## 行为

智能问答选择图片后，前端通过 multipart `images` 上传实际文件，支持只发图片。后端校验并压缩图片，通过千问视觉模型流式回答，不再把图片问题当成纯文本联网搜索。此分支直接调用视觉模型，未经过课堂 Agent 的检索/筛选工具循环，也不自动融合课堂纪要。普通文字提问继续走原路径。

图片保存在 Django 默认存储：配置 OSS 时使用 OSS，否则使用本地 media。问答记录的 `images` 字段保存文件引用，历史接口返回新生成的访问地址；重新生成通过 `image_record_id` 读取当前用户、当前对话的原图，不依赖过期签名地址。完成后才保存回答；模型失败、截断或中途断开时清理未完成附件。删除对话后清理图片。

支持 JPG、PNG、WebP、GIF（首帧），每次最多 8 张、单张 10MB、合计 40MB、单张不超过 2500 万像素。处理后长边最多 2400 像素，转为 JPEG 并去除元数据。图片内容通过请求内的图像数据发送给模型；OSS 用于保存和展示，并不会免除视觉 token 费用。新发的纯文字消息不会自动携带上一轮图片，需要再次上传图片；原消息的重新生成可以复用图片。

## 配置与部署

使用后端共享 `.env`（线上使用服务环境文件/Secrets），不要把密钥提交到 Git：

```dotenv
DASHSCOPE_API_KEY=填写现有百炼密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
AGENT_VISION_MODEL=qwen3-vl-plus
AGENT_TIMEOUT=30
```

该账号和对应地域必须能调用视觉模型。不需要新增 Tavily key；现有 OSS 配置继续使用。前端和后端需要一起更新，前端执行 `npm ci`、`npm run build`。

在后端目录、部署服务使用的 Python 环境中执行：

```sh
python mysite/manage.py showmigrations qa
python mysite/manage.py migrate --plan
python mysite/manage.py migrate
```

再重启后端服务并发布新前端。反向代理上传限制应大于 40MB（例如 `client_max_body_size 50m`）；SSE 保持禁用缓冲并留足模型响应超时；使用本地存储时应正确提供 `/media/`，使用 OSS 时浏览器需要能访问返回的签名 HTTPS 地址。

### 与线上迁移合并

本地当前迁移基线为 `0007_backfill_sessions`，本次新增 `0008_qarecord_images`。如果线上已有另一份 `qa/0008_*`，需要保留两边迁移文件，先检查各自操作；若为独立字段变更，可在合并代码后通过 `python mysite/manage.py makemigrations qa --merge` 生成合并迁移、检查后提交，再执行 migrate。不要覆盖线上迁移，不要用 `--fake` 掩盖冲突。如果线上 `0008` 已添加同名字段，需按真实迁移内容调整，不能直接合并重复 AddField。

## 验证

```sh
python mysite/manage.py test qa.test_images qa.test_sessions qa.test_agent_integration notes.test_oss notes.test_oss_storage --settings=mysite.oss_test_settings
```

前端：`node --test tests/qaPage.test.mjs tests/streamAsk.test.mjs tests/qaHistory.test.mjs`、`npm run build`。

手工验收：登录后发图片和问题（以及只发图片），确认逐步输出；刷新/重新登录查看原图；重新生成原消息；无效图片提示错误。PDF、PPT、音频等请在课堂纪要上传，此处暂只开放图片。
