"""Registered asset -> extraction/ASR -> chunking -> embeddings -> atomic index."""

from .materials import chunk_segments


class Ingestor:
    def __init__(self, knowledge, extractor, speech=None):
        self.knowledge = knowledge
        self.extractor = extractor
        self.speech = speech

    def ingest(self, asset_id, context):
        # Authorization must happen before reading a path or sending any network request.
        asset = self.knowledge.asset(context, asset_id)
        yield {'stage': 'extracting', 'asset_id': asset_id, 'name': asset['name']}
        if asset['kind'] == 'audio':
            if self.speech is None:
                raise ValueError('未配置 ASR 服务')
            segments = None
            for event in self.speech.transcribe(asset['location'], asset['name']):
                if event['stage'] == 'transcribed':
                    segments = event['segments']
                else:
                    yield event
            if not segments:
                raise ValueError('录音未产生可用材料')
        else:
            segments = self.extractor.extract(asset['location'])
        chunks = chunk_segments(segments)
        yield {'stage': 'embedding', 'chunks': len(chunks)}
        vectors = []
        for start in range(0, len(chunks), 10):
            batch = chunks[start:start + 10]
            vectors.extend(self.knowledge.embedder.embed([s.text for s in batch]))
            yield {'stage': 'embedding', 'completed': min(start + 10, len(chunks)), 'chunks': len(chunks)}
        self.knowledge.replace_source(context, asset_id, chunks, vectors, original_segments=segments)
        yield {'stage': 'indexed', 'asset_id': asset_id, 'segments': len(segments), 'chunks': len(chunks), 'degraded': False}
