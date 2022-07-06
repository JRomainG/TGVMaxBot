import json
import logging
from enum import Enum
from datetime import datetime, timedelta
from typing import List, Callable, Optional

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, ForceReply
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from utils import Requests, reshape, getlist
from backend import Trip, TGVBot


class BotState(Enum):
    SETUP = 0  # Choose action to perform
    SELECT_ORIGIN = 1  # Choose start station
    SELECT_DESTINATION = 2  # Choose arrival station
    SELECT_MIN_DATE = 3  # Choose departure earliest target date
    SELECT_MAX_DATE = 4  # Choose departure latest target date
    SELECT_MAX_DURATION = 5  # Choose max allowed trip duration
    SELECT_TRIP = 6  # Choose trip to delete


class BotProfile:
    def __init__(
        self,
        name: str,
        allowed_chat_ids: List[int],
        allowed_user_ids: List[int],
        interval: int,
        silent: bool,
    ):
        self.name = name
        self.allowed_chat_ids = allowed_chat_ids
        self.allowed_user_ids = allowed_user_ids
        self.interval = interval
        self.silent = silent

    @staticmethod
    def from_config(name: str, config: dict) -> "BotProfile":
        return BotProfile(
            name=name,
            allowed_chat_ids=getlist(config, "allowed_chat_ids", int),
            allowed_user_ids=getlist(config, "allowed_user_ids", int),
            interval=config.getint("check_interval", 3600),
            silent=config.getboolean("silent_notifications", False),
        )


class Bot:
    def __init__(self, token: str, profiles: BotProfile):
        self.init_stations()
        self.init_application(token)
        self.profiles = profiles
        self.backend = TGVBot(self.application, 600)

    def init_stations(self):
        # Get all available stations from the API
        logging.info("Fetching list of stations")
        self.origin_stations = sorted(
            json.loads(
                Requests.do_get(
                    "https://sncf-simulateur-api-prod.azurewebsites.net/api/Stations/AllOrigins"
                )
            )
        )
        self.destination_stations = sorted(
            json.loads(
                Requests.do_get(
                    "https://sncf-simulateur-api-prod.azurewebsites.net/api/Stations/AllDestinations"
                )
            )
        )
        logging.info(
            "Found %d origin stations and %d destination stations",
            len(self.origin_stations),
            len(self.destination_stations),
        )

        # Generate keyboards for all those stations
        self._origin_keyboard = reshape(self.origin_stations, 3)
        self._destination_keyboard = reshape(self.destination_stations, 3)

    def init_application(self, token: str):
        self.application = (
            Application.builder().token(token).concurrent_updates(False).build()
        )
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", self.start)],
            states={
                BotState.SETUP: [
                    MessageHandler(filters.Regex(r"^Start trip$"), self.origin),
                    MessageHandler(filters.Regex(r"^Delete trip$"), self.select_trip),
                    MessageHandler(filters.Regex(r"^List trips$"), self.list_trips),
                    MessageHandler(filters.Regex(r"^Cancel$"), self.cancel),
                ],
                BotState.SELECT_ORIGIN: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.destination),
                ],
                BotState.SELECT_DESTINATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.min_date),
                ],
                BotState.SELECT_MIN_DATE: [
                    MessageHandler(
                        filters.Regex(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
                        self.max_date,
                    )
                ],
                BotState.SELECT_MAX_DATE: [
                    MessageHandler(
                        filters.Regex(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"),
                        self.max_duration,
                    )
                ],
                BotState.SELECT_MAX_DURATION: [
                    MessageHandler(
                        filters.Regex(r"^\d+:\d{2}$"),
                        self.create_trip,
                    )
                ],
                BotState.SELECT_TRIP: [
                    MessageHandler(filters.Regex(r"^\d+$"), self.delete_trip),
                    MessageHandler(filters.Regex(r"^Cancel$"), self.cancel),
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            conversation_timeout=timedelta(minutes=30),
            per_chat=True,
        )
        self.application.add_handler(conv_handler)
        self.application.add_handler(CommandHandler("chatid", self.get_chat_id))
        self.application.add_handler(CommandHandler("userid", self.get_user_id))

    def run(self):
        """
        Runs the application until ctrl+c is sent
        """
        self.application.run_polling()

    def _get_profile(self, context: ContextTypes.DEFAULT_TYPE) -> Optional[BotProfile]:
        chat_id = context._chat_id
        user_id = context._user_id
        for profile in self.profiles:
            if chat_id in profile.allowed_chat_ids:
                logging.debug(
                    "Profile found for chat with ID %s: %s", chat_id, profile.name
                )
                return profile
            if user_id in profile.allowed_user_ids:
                logging.debug(
                    "Profile found for user with ID %s: %s", user_id, profile.name
                )
                return profile
        return None

    def check_authorized(func: Callable) -> bool:
        def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            if self._get_profile(context) is not None:
                return func(self, update, context)
            else:
                return ConversationHandler.END

        return wrapper

    @check_authorized
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Starts the conversation and asks the user what they want to do
        """
        reply_keyboard = [["Start trip", "Delete trip", "List trips", "Cancel"]]
        await update.message.reply_text(
            "Welcome to the TGV Max reservation bot! You can send /cancel to stop talking to me.\n"
            "What do you want to do?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard,
                one_time_keyboard=True,
                input_field_placeholder="Action",
                selective=True,
            ),
        )
        return BotState.SETUP

    async def _send_origin_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str
    ):
        await update.message.reply_text(
            message,
            reply_markup=ReplyKeyboardMarkup(
                self._origin_keyboard,
                one_time_keyboard=True,
                input_field_placeholder="Origin train station",
                selective=True,
            ),
        )

    @check_authorized
    async def origin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Asks the user to choose from which station they should leave
        """
        await self._send_origin_message(
            update, context, "From where do you want to leave?"
        )
        return BotState.SELECT_ORIGIN

    async def _send_destination_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str
    ):
        await update.message.reply_text(
            message,
            reply_markup=ReplyKeyboardMarkup(
                self._destination_keyboard,
                one_time_keyboard=True,
                input_field_placeholder="Destination train station",
                selective=True,
            ),
        )

    @check_authorized
    async def destination(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Asks the user to choose at which station they should arrive
        """
        if update.message.text not in self.origin_stations:
            self._send_origin_message(
                update, context, "Please select a valid station from the list"
            )
            return BotState.SELECT_ORIGIN

        context.user_data["origin"] = update.message.text
        await self._send_destination_message(
            update, context, "Where do you want to go?"
        )
        return BotState.SELECT_DESTINATION

    async def _send_min_date_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str
    ):
        await update.message.reply_text(
            message,
            reply_markup=ForceReply(
                selective=True, input_field_placeholder="yyyy-mm-dd HH:MM:SS"
            ),
        )

    @check_authorized
    async def min_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Asks the user to choose the earliest acceptable departure date
        """
        if update.message.text not in self.destination_stations:
            await self._send_destination_message(
                update, context, "Please select a valid station from the list"
            )
            return BotState.SELECT_DESTINATION
        elif update.message.text == context.user_data["origin"]:
            await self._send_destination_message(
                update,
                context,
                "Please selection a destination different from the origin station",
            )
            return BotState.SELECT_DESTINATION

        context.user_data["destination"] = update.message.text
        await self._send_min_date_message(
            update,
            context,
            "When do you want to leave at the earliest? Date format: yyyy-mm-dd HH:MM:SS",
        )
        return BotState.SELECT_MIN_DATE

    async def _send_max_date_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str
    ):
        await update.message.reply_text(
            message,
            reply_markup=ForceReply(
                selective=True, input_field_placeholder="yyyy-mm-dd HH:MM:SS"
            ),
        )

    @check_authorized
    async def max_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Asks the user to choose the latest acceptable departure date
        """
        try:
            context.user_data["min_date"] = Trip.parse_date(update.message.text)
        except ValueError:
            logging.warning("Failed to parse min_date %s: %s", update.message.text, e)
            await self._send_min_date_message(
                update,
                context,
                "Please enter a valid date",
            )
            return BotState.SELECT_MIN_DATE

        await self._send_max_date_message(
            update,
            context,
            "When do you want to leave at the latest? Date format: yyyy-mm-dd HH:MM:SS",
        )
        return BotState.SELECT_MAX_DATE

    async def _send_max_duration_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, message: str
    ):
        await update.message.reply_text(
            message,
            reply_markup=ForceReply(selective=True, input_field_placeholder="HH:MM"),
        )

    @check_authorized
    async def max_duration(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Asks the user to choose the maximum trip duration they want
        """
        try:
            context.user_data["max_date"] = Trip.parse_date(update.message.text)
        except ValueError as e:
            logging.warning("Failed to parse max_date %s: %s", update.message.text, e)
            await self._send_max_date_message(
                update,
                context,
                "Please enter a valid date",
            )
            return BotState.SELECT_MAX_DATE

        min_date = context.user_data["min_date"]
        max_date = context.user_data["max_date"]
        if max_date < min_date:
            await self._send_max_date_message(
                update,
                context,
                "Latest date must be after earliest date",
            )
            return BotState.SELECT_MAX_DATE

        if (
            max_date.year != min_date.year
            or max_date.month != min_date.month
            or max_date.day != min_date.day
        ):
            await self._send_max_date_message(
                update,
                context,
                "Because of technical limitations in the SNCF API, only same-day trips can be planned",
            )
            return BotState.SELECT_MAX_DATE

        if max_date < datetime.now():
            await self._send_max_date_message(
                update,
                context,
                "Latest date cannot be in the past",
            )
            return BotState.SELECT_MAX_DATE

        await self._send_max_duration_message(
            update,
            context,
            "What is the maximum trip duration that you'll allow? Duration format: HH:MM",
        )
        return BotState.SELECT_MAX_DURATION

    @check_authorized
    async def create_trip(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Creates a trip and stores it to later notify the user if tickets are
        available
        """
        try:
            context.user_data["max_duration"] = Trip.parse_duration(update.message.text)
        except ValueError as e:
            logging.warning(
                "Failed to parse max_duration %s: %s", update.message.text, e
            )
            await self._send_max_duration_message(
                update,
                context,
                "Please enter a valid duration",
            )
            return BotState.SELECT_MAX_DURATION

        try:
            trip = Trip.from_config(context.user_data)
            self.backend.add_trip(context, trip)
            await update.message.reply_text(
                f"Created {trip}.\nUse /start to list all your existing trips",
                reply_markup=ReplyKeyboardRemove(),
            )
        except Exception as e:
            logging.warning("create_trip failed with data %s: %s", context.user_data, e)
            await update.message.reply_text(
                "Sorry, trip creation failed, please try again",
                reply_markup=ReplyKeyboardRemove(),
            )

        return ConversationHandler.END

    @check_authorized
    async def select_trip(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Asks the user to choose the trip to delete
        """
        try:
            trips = self.backend.get_trips(context)
            if not trips:
                raise KeyError("No trips")

            msg = "Select a trip:"
            for i, trip in enumerate(trips):
                msg += f"\n{i}: {trip}"

            trip_ids = list(range(len(trips)))
            await update.message.reply_text(
                msg,
                reply_markup=ReplyKeyboardMarkup(
                    reshape(trip_ids, 5) + [["Cancel"]],
                    one_time_keyboard=True,
                    input_field_placeholder="Trip ID",
                    selective=True,
                ),
            )
        except KeyError:
            await update.message.reply_text(
                "You have no stored trips", reply_markup=ReplyKeyboardRemove()
            )
            return ConversationHandler.END

        return BotState.SELECT_TRIP

    @check_authorized
    async def delete_trip(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Deletes a stored trip
        """
        try:
            trip_id = int(update.message.text)
            trip = self.backend.get_trip(context, trip_id)
            self.backend.remove_trip(context, trip_id)
            await update.message.reply_text(
                f"Deleted {trip}", reply_markup=ReplyKeyboardRemove()
            )
        except (KeyError, IndexError):
            await update.message.reply_text(
                "Unknown trip, aborting", reply_markup=ReplyKeyboardRemove()
            )
        except Exception as e:
            logging.warning("delete_trip failed with data %s: %s", context.user_data, e)
            await update.message.reply_text(
                "Sorry, trip deletion failed, please try again later",
                reply_markup=ReplyKeyboardRemove(),
            )
        return ConversationHandler.END

    @check_authorized
    async def list_trips(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        List a user's saved trips
        """
        try:
            trips = self.backend.get_trips(context)
            if not trips:
                raise KeyError("No trips")

            msg = "Your trips:"
            for trip in trips:
                msg += f"\n- {trip}"

            await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
        except KeyError:
            await update.message.reply_text(
                "You have no stored trips", reply_markup=ReplyKeyboardRemove()
            )
        return ConversationHandler.END

    @check_authorized
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Cancels and ends the conversation
        """
        await update.message.reply_text(
            "Conversation canceled, see you later!", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    async def get_chat_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Sends a message containing the current chat's ID
        """
        await update.message.reply_text(f"Current chat ID: {context._chat_id}")

    async def get_user_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Sends a message containing the user's ID
        """
        await update.message.reply_text(f"Current user ID: {context._user_id}")
