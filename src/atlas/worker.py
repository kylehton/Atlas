import logging

logger = logging.getLogger(__name__)


def run() -> None:
    logger.info("Atlas worker started")


if __name__ == "__main__":
    run()

