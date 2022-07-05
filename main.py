#!/usr/bin/env python3
import logging
import argparse
import configparser

from bot import Bot, BotProfile


def main():
    # Load the Telegram auth info
    auth_config = configparser.ConfigParser()
    auth_config.read("auth.ini")

    # Load all the profiles from the config file
    config = configparser.ConfigParser()
    config.read("config.ini")

    profiles = []
    for section in config.sections():
        profile = BotProfile.from_config(section, config[section])
        assert (
            len(profile.allowed_chat_ids) > 0 or len(profile.allowed_user_ids) > 0
        ), ValueError("Profile must have at least one allowed chat ID or user ID")
        profiles.append(profile)

    assert len(profiles) > 0, ValueError(
        "At least one profile must be defined in config.ini"
    )

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
