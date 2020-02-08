import logging
from sys import stdout

from src.spider.spider import Spider


# TODO: move AWS specific stuff into their own module?
#       Use adapter pattern to easily switch storage platform.
#       Create an AWS and a local storage adapter.


if __name__ == "__main__":
    logging.basicConfig(
        stream=stdout,
        level=logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        spider = Spider()
        spider.crawl()
    except RuntimeError as e:
        logging.error(e)
    finally:
        logging.info("script end")
