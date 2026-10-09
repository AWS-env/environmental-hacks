# Synthetic OBS-04 fixture: a command-line tool that writes to a terminal. Never executed.
import argparse
import logging

logger = logging.getLogger(__name__)


def main():
    args = argparse.ArgumentParser().parse_args()
    logger.info("parsed %s", args)
