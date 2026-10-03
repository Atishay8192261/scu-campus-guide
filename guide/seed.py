import asyncio
import json

import httpx

from guide.database import Store
from guide.retrieval import SEEDS, Retriever
from guide.settings import Settings


async def main():
    settings = Settings()
    store = Store(settings.database_url)
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        retriever = Retriever(client, store)
        result = []
        for source in SEEDS:
            found = await retriever.fetch_many([source["url"]], fresh=True)
            result.append(
                {
                    "url": source["url"],
                    "fetched": bool(found),
                    "characters": len(found[0].text) if found else 0,
                }
            )
        print(json.dumps(result, indent=2))
    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
