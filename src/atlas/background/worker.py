import asyncio
import logging

logger = logging.getLogger(__name__)


async def serve() -> None:
    logger.info("Atlas worker started")
    await asyncio.Event().wait()


def run() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    run()
