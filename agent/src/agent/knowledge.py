"""Small persistent RAG backend using SQLite and exact cosine retrieval."""

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .gateway import web_url
from .materials import timestamp
from .media import validate_vector
from .schemas import Chunk, Material


class KnowledgeBase:
    def __init__(self, path, embedder):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedder = embedder
        self._signature = embedder.signature
        with self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notes (
                    tenant TEXT, user TEXT, note TEXT, title TEXT NOT NULL, course TEXT NOT NULL,
                    PRIMARY KEY (tenant,user,note));
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, tenant TEXT, user TEXT, note TEXT,
                    kind TEXT NOT NULL, location TEXT NOT NULL, name TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS assets_scope ON assets(tenant,user,note);
                CREATE TABLE IF NOT EXISTS chunks (
                    asset TEXT, tenant TEXT, user TEXT, note TEXT, ordinal INTEGER,
                    text TEXT NOT NULL, ref TEXT NOT NULL, timestamp_ms INTEGER, vector TEXT NOT NULL,
                    PRIMARY KEY (asset,ordinal));
                CREATE INDEX IF NOT EXISTS chunks_scope ON chunks(tenant,user,note);
                CREATE TABLE IF NOT EXISTS source_materials (
                    asset TEXT PRIMARY KEY, tenant TEXT, user TEXT, note TEXT, segments TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS materials_scope ON source_materials(tenant,user,note);
            ''')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('embedding', self._signature))
            if db.execute('SELECT value FROM metadata WHERE key=?', ('embedding',)).fetchone()[0] != self._signature:
                raise ValueError('索引的嵌入模型/维度不匹配；请新建索引并重新导入')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _scope(context):
        if not context.note_id.strip():
            raise ValueError('入库和检索必须指定纪要 ID')
        return context.tenant_id, context.user_id, context.note_id

    def _authorize(self, db, context):
        row = db.execute('SELECT title,course FROM notes WHERE tenant=? AND user=? AND note=?', self._scope(context)).fetchone()
        if row is None:
            raise PermissionError('纪要不存在或无访问权限')
        return row

    def register_note(self, context, *, title, course=''):
        """Host-only provisioning: context MUST originate from authenticated identity."""
        if not title.strip():
            raise ValueError('纪要标题不能为空')
        with self._connection() as db:
            db.execute('INSERT INTO notes VALUES (?,?,?,?,?) ON CONFLICT(tenant,user,note) DO UPDATE SET title=excluded.title,course=excluded.course', (*self._scope(context), title, course))

    def _register(self, context, kind, location, name):
        with self._connection() as db:
            self._authorize(db, context)
            identity = json.dumps([*self._scope(context), kind, location], ensure_ascii=False)
            asset_id = hashlib.sha256(identity.encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?,?)', (asset_id, *self._scope(context), kind, location, name))
        return asset_id

    def register_file(self, context, path):
        """Called by the upload handler, never with a model-provided local path."""
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise ValueError('材料必须是文件')
        return self._register(context, 'file', str(path), path.name)

    def register_audio(self, context, audio_url, name='课堂录音'):
        if not web_url(audio_url) or not audio_url.startswith('https://'):
            raise ValueError('录音必须提供 HTTPS URL')
        return self._register(context, 'audio', audio_url, name)

    def asset(self, context, asset_id):
        with self._connection() as db:
            self._authorize(db, context)
            row = db.execute('SELECT * FROM assets WHERE id=? AND tenant=? AND user=? AND note=?', (asset_id, *self._scope(context))).fetchone()
            if row is None:
                raise PermissionError('材料不存在或无访问权限')
            return dict(row)

    def replace_source(self, context, asset_id, segments, vectors, original_segments=None):
        if self.embedder.signature != self._signature:
            raise ValueError('不能混用不同嵌入模型')
        if not segments or len(segments) != len(vectors):
            raise ValueError('材料分块与向量数量不一致')
        vectors = [validate_vector(v, self.embedder.dimensions) for v in vectors]
        self.asset(context, asset_id)
        with self._connection() as db:
            self._authorize(db, context)
            db.execute('INSERT INTO source_materials VALUES (?,?,?,?,?) ON CONFLICT(asset) DO UPDATE SET segments=excluded.segments',
                       (asset_id, *self._scope(context), json.dumps([s.model_dump() for s in (original_segments if original_segments is not None else segments)], ensure_ascii=False)))
            db.execute('DELETE FROM chunks WHERE asset=? AND tenant=? AND user=? AND note=?', (asset_id, *self._scope(context)))
            db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)', [
                (asset_id, *self._scope(context), i, s.text, s.ref, s.timestamp_ms, json.dumps(v))
                for i, (s, v) in enumerate(zip(segments, vectors))
            ])

    def material(self, context):
        with self._connection() as db:
            note = self._authorize(db, context)
            sources = db.execute('SELECT segments FROM source_materials WHERE tenant=? AND user=? AND note=? ORDER BY rowid', self._scope(context)).fetchall()
        rows = [segment for source in sources for segment in json.loads(source['segments'])]
        lines = []
        for row in rows:
            prefix = f"[{timestamp(row['timestamp_ms'])}] " if row['timestamp_ms'] is not None else ''
            lines.append(f"{prefix}{row['text']}（{row['ref']}）")
        return Material(title=note['title'], course=note['course'], text='\n'.join(lines))

    def retrieve(self, question, context, limit=4):
        if not question.strip() or not 1 <= limit <= 20:
            raise ValueError('检索参数无效')
        if self.embedder.signature != self._signature:
            raise ValueError('不能混用不同嵌入模型')
        with self._connection() as db:
            self._authorize(db, context)
            rows = db.execute('SELECT text,ref,vector FROM chunks WHERE tenant=? AND user=? AND note=?', self._scope(context)).fetchall()
        if not rows:
            return []
        query_vectors = self.embedder.embed([question])
        if len(query_vectors) != 1:
            raise ValueError('查询向量结果数量无效')
        query = validate_vector(query_vectors[0], self.embedder.dimensions)
        norm = math.hypot(*query)
        hits = []
        for row in rows:
            vector = validate_vector(json.loads(row['vector']), self.embedder.dimensions)
            vector_norm = math.hypot(*vector)
            score = sum((a / norm) * (b / vector_norm) for a, b in zip(query, vector))
            hits.append(Chunk(text=row['text'], ref=row['ref'], score=max(-1., min(1., score))))
        return sorted(hits, key=lambda c: c.score, reverse=True)[:limit]

    def profile(self, context):
        return None
