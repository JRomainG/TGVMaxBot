#!/usr/bin/env python3
import logging
import argparse
import configparser

from bot import Bot, BotProfile
from utils import getintlist


def main():
    # Load the Telegram auth info
    auth_config = configparser.ConfigParser()
    auth_config.read("auth.ini")

    # Load all the profiles from the config file
    config = configparser.ConfigParser()
    config.read("config.ini")

    profiles = []
    for section in config.sections():
        allowed_chat_ids = getintlist(config[section], "allowed_chat_ids")
        allowed_user_ids = getintlist(config[section], "allowed_user_ids")
        interval = config[section].getint("check_interval", 3600)
        silent = config[section].getboolean("silent_notifications", False)
        profile = BotProfile(section, allowed_chat_ids, allowed_user_ids, interval, silent)
        profiles.append(profile)

    # Create a bot with this info
    bot = Bot(auth_config["Telegram"]["token"], profiles)
    bot.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TGVMaxBot")
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
