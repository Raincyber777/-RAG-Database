"""Application-owned data access. No model-supplied identity or SQL."""

from typing import Protocol

from .schemas import Chunk, Material, UserContext


class Backend(Protocol):
    """Implement using the application's existing authenticated data layer.

    Every method MUST enforce tenant, user and note access using context.
    retrieve returns actual cosine scores (text-embedding-v4, 1024 dimensions),
    not reranker scores or distances. Raise PermissionError on denied access.
    """

    def material(self, context: UserContext) -> Material: ...

    def retrieve(self, question: str, context: UserContext, limit: int = 4) -> list[Chunk]: ...

    def profile(self, context: UserContext) -> dict | None: ...


class EmptyBackend:
    """Useful for startup before a backend is injected; never invents data."""

    def material(self, context):
        return Material(title='当前课堂纪要', course='', text='')

    def retrieve(self, question, context, limit=4):
        return []

    def profile(self, context):
        return None
