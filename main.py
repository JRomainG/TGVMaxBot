#!/usr/bin/env python3
import logging
import argparse
import configparser

from bot import Bot


def main():
    # Load the Telegram auth info
    auth_config = configparser.ConfigParser()
    auth_config.read("auth.ini")

    # Create a bot with this info
    bot = Bot(auth_config["Telegram"]["token"])
    bot.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PankoBot")
    parser.add_argument(
        "-l",
        "--log-level",
        default="INFO",
        help="logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    args = parser.parse_args()

    level = args.log_level.upper()
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=level,
    )
    main()
