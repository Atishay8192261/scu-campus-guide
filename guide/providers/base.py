from typing import Protocol

from pydantic import BaseModel


class ProviderError(Exception):
    pass


class JsonModel(Protocol):
    async def generate(
        self, instruction: str, data: dict, schema: type[BaseModel]
    ) -> BaseModel: ...


class SearchProvider(Protocol):
    async def search(self, query: str) -> list[str]: ...
