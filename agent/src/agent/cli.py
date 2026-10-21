"""Offline examples plus explicit local ingestion and streaming Q&A commands."""

import argparse
import json

from .backend import EmptyBackend
from .gateway import DashScopeGateway, Settings
from .schemas import Material, UserContext
from .services import StudyService
from .tools import build_tools


def main():
    parser = argparse.ArgumentParser(description='智学纪要 Agent；后端接入方式见 README.md')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--list-tools', action='store_true', help='列出 Agent 工具，不访问网络')
    group.add_argument('--demo', action='store_true', help='运行无密钥的课堂纪要规则降级示例')
    commands = parser.add_subparsers(dest='command')
    ingest = commands.add_parser('ingest', help='抽取/转写材料并向量化入库（使用真实模型）')
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument('--file', help='TXT/MD/DOCX/PDF/PPTX/图片的本地路径')
    source.add_argument('--audio-url', help='可供百炼读取的 HTTPS 录音地址')
    ingest.add_argument('--title', required=True)
    ingest.add_argument('--course', default='')
    ask = commands.add_parser('ask', help='检索已入库材料并问答（使用真实模型）')
    ask.add_argument('--question', required=True)
    ask.add_argument('--stream', action='store_true', help='逐行输出 JSON 流式事件')
    ask.add_argument('--thread-id', default='cli')
    for command in (ingest, ask):
        command.add_argument('--db', default='data/knowledge.db')
        command.add_argument('--user-id', required=True)
        command.add_argument('--tenant-id', required=True)
        command.add_argument('--note-id', required=True)
    args = parser.parse_args()
    if args.command:
        if args.list_tools or args.demo:
            parser.error('离线示例参数不能与 ingest/ask 同时使用')
        return _online(args, parser)
    if not args.list_tools and not args.demo:
        parser.print_help()
        return

    class DemoBackend(EmptyBackend):
        def material(self, context):
            return Material(title='导数入门', course='微积分', text='[08:15] 导数描述函数的瞬时变化率。\n[12:30] 老师要求复习导数的定义。')

    gateway = DashScopeGateway(Settings(api_key=''))
    try:
        service = StudyService(DemoBackend(), gateway)
        if args.list_tools:
            result = [{'name': tool.name, 'description': tool.description} for tool in build_tools(service)]
        else:
            result = service.summarize(UserContext('demo-user', 'demo-tenant', 'demo-note'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        gateway.close()


def _online(args, parser):
    from .extraction import Extractor
    from .ingestion import Ingestor
    from .knowledge import KnowledgeBase
    from .media import Embedder, SpeechTranscriber
    from .runtime import open_agent

    settings = Settings.from_env()
    if not settings.api_key:
        parser.error('请先配置 DASHSCOPE_API_KEY；离线体验可运行 --demo')
    gateway = DashScopeGateway(settings)
    try:
        knowledge = KnowledgeBase(args.db, Embedder(gateway))
        context = UserContext(args.user_id, args.tenant_id, args.note_id)
        if args.command == 'ingest':
            knowledge.register_note(context, title=args.title, course=args.course)
            asset = knowledge.register_file(context, args.file) if args.file else knowledge.register_audio(context, args.audio_url, args.title)
            ingestor = Ingestor(knowledge, Extractor(ocr=gateway.vision_bytes), SpeechTranscriber(gateway))
            for progress in ingestor.ingest(asset, context):
                print(json.dumps(progress, ensure_ascii=False), flush=True)
        else:
            with open_agent(knowledge, settings=settings, gateway=gateway) as assistant:
                if args.stream:
                    for event in assistant.stream(args.question, context=context, thread_id=args.thread_id):
                        print(json.dumps(event, ensure_ascii=False), flush=True)
                else:
                    print(json.dumps(assistant.invoke(args.question, context=context, thread_id=args.thread_id), ensure_ascii=False, indent=2))
    except (ValueError, PermissionError, FileNotFoundError):
        parser.exit(1, '材料、身份或索引配置无效；请检查格式、纪要 ID、权限和嵌入模型配置。\n')
    except Exception:
        parser.exit(1, '处理失败，未完成入库；请检查模型配置、录音链接和网络连接。\n')
    finally:
        gateway.close()
