"""Tiny periodic scheduler for the sweep actor: python -m ue_api.scheduler"""

import logging
import time

from ue_api.workers import sweep_pending

INTERVAL_SECONDS = 30


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        sweep_pending.send()
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
