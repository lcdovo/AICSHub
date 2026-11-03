"""仅本次UI合成库的独立索引worker，复用server安全资源检查。"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from acceptance.m3.server import configure_kb
import asyncio

configure_kb()


async def main():
    from app.services.background_jobs import tick
    from app.utils.database import async_session, engine

    count = 0
    while count < 10 and await tick(async_session, kind="knowledge_index"):
        count += 1
    await engine.dispose()
    print("synthetic UI jobs consumed:", count)


if __name__ == "__main__":
    asyncio.run(main())
