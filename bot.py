import json
import logging
from enum import Enum
from datetime import timedelta

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from utils import Requests, reshape
from backend import Trip, TGVBot


class BotState(Enum):
    SETUP = 0  # Choose action to perform
    SELECT_ORIGIN = 1  # Choose start station
    SELECT_DESTINATION = 2  # Choose arrival station
    SELECT_MIN_DATE = 3  # Choose departure earliest target date
    SELECT_MAX_DATE = 4  # Choose departure latest target date
    SELECT_TRIP = 5  # Choose trip to delete


class Bot:
    def __init__(self, token: str):
        self.init_stations()
        self.init_application(token)
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
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.destination)
                ],
                BotState.SELECT_DESTINATION: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.min_date)
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
                        self.create_trip,
                    )
                ],
                BotState.SELECT_TRIP: [
                    MessageHandler(filters.Regex(r"^\d+$"), self.delete_trip)
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
            conversation_timeout=timedelta(minutes=30),
            per_chat=True,
        )
        self.application.add_handler(conv_handler)

    def run(self):
        """
        Runs the application until ctrl+c is sent
        """
        self.application.run_polling()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Starts the conversation and asks the user what they want to do
        """
        reply_keyboard = [["Start trip", "Delete trip", "List trips", "Cancel"]]
        await update.message.reply_text(
            "Welcome to the TGV Max reservation bot!\n"
            "You can send /cancel to stop talking to me.\n"
            "What do you want to do?",
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard,
                one_time_keyboard=True,
                input_field_placeholder="Action",
                selective=True,
            ),
        )
        return BotState.SETUP

    async def origin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Asks the user to choose from which station they should leave
        """
        await update.message.reply_text(
            "From where do you want to leave?",
            reply_markup=ReplyKeyboardMarkup(
                self._origin_keyboard,
                one_time_keyboard=True,
                input_field_placeholder="Origin train station",
                selective=True,
            ),
        )
        return BotState.SELECT_ORIGIN

    async def destination(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Asks the user to choose at which station they should arrive
        """
        if update.message.text not in self.origin_stations:
            await update.message.reply_text(
                "Please select a valid station from the list",
                reply_markup=ReplyKeyboardMarkup(
                    self._origin_keyboard,
                    one_time_keyboard=True,
                    input_field_placeholder="Origin train station",
                    selective=True,
                ),
            )
            return BotState.SELECT_ORIGIN

        context.user_data["origin"] = update.message.text
        await update.message.reply_text(
            "Where do you want to go?",
            reply_markup=ReplyKeyboardMarkup(
                self._destination_keyboard,
                one_time_keyboard=True,
                input_field_placeholder="Destination train station",
                selective=True,
            ),
        )
        return BotState.SELECT_DESTINATION

    async def min_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Asks the user to choose the earliest acceptable departure date
        """
        error = None
        if update.message.text not in self.destination_stations:
            error = "Please select a valid station from the list"
        elif update.message.text == context.user_data["origin"]:
            error = "Please selection a destination different from the origin station"

        if error is not None:
            await update.message.reply_text(
                error,
                reply_markup=ReplyKeyboardMarkup(
                    self._destination_keyboard,
                    one_time_keyboard=True,
                    input_field_placeholder="Destination train station",
                    selective=True,
                ),
            )
            return BotState.SELECT_DESTINATION
        else:
            context.user_data["destination"] = update.message.text
            await update.message.reply_text(
                "When do you want to leave at the earliest? Date format: yyyy-mm-dd HH:MM:SS",
            )
            return BotState.SELECT_MIN_DATE

    async def max_date(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Asks the user to choose the latest acceptable departure date
        """
        try:
            context.user_data["min_date"] = Trip.parse_date(update.message.text)
        except ValueError:
            await update.message.reply_text(
                "Please enter a valid date",
            )
            return BotState.SELECT_MIN_DATE

        await update.message.reply_text(
            "When do you want to leave at the latest? Date format: yyyy-mm-dd HH:MM:SS",
        )
        return BotState.SELECT_MAX_DATE

    async def create_trip(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> int:
        """
        Creates a trip and stores it to later notify the user if tickets are
        available
        """
        try:
            context.user_data["max_date"] = Trip.parse_date(update.message.text)
        except ValueError as e:
            await update.message.reply_text(
                "Please enter a valid date",
            )
            return BotState.SELECT_MAX_DATE

        if context.user_data["max_date"] < context.user_data["min_date"]:
            await update.message.reply_text(
                "Latest date must be after earliest date",
            )
            return BotState.SELECT_MAX_DATE

        try:
            d = context.user_data
            trip = Trip(d["origin"], d["destination"], d["min_date"], d["max_date"])
            self.backend.add_trip(context, trip)
            await update.message.reply_text(
                f"Created {trip}.\nUse /start to list all your existing trips",
            )
        except Exception as e:
            logging.warning("create_trip failed with data %s: %s", context.user_data, e)
            await update.message.reply_text(
                "Sorry, trip creation failed, please try again",
            )

        return ConversationHandler.END

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

            msg = "Select a trip:\n"
            for trip in trips:
                msg += f"0: {trip}"

            await update.message.reply_text(
                msg,
                reply_markup=ReplyKeyboardMarkup(
                    reshape(list(range(len(trips))), 5),
                    one_time_keyboard=True,
                    input_field_placeholder="Trip ID",
                    selective=True,
                ),
            )
        except KeyError:
            await update.message.reply_text(
                "You have no stored trips",
            )
            return ConversationHandler.END

        return BotState.SELECT_TRIP

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
                f"Deleted {trip}",
            )
        except KeyError:
            await update.message.reply_text(
                "Unknown trip, please try again",
            )
            return BotState.SELECT_TRIP
        except Exception as e:
            logging.warning("delete_trip failed with data %s: %s", context.user_data, e)
            await update.message.reply_text(
                "Sorry, trip deletion failed, please try again",
            )
        return ConversationHandler.END

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

            msg = "Your trips:\n"
            for trip in trips:
                msg += f"- {trip}"

            await update.message.reply_text(msg)
        except KeyError:
            await update.message.reply_text(
                "You have no stored trips",
            )
        return ConversationHandler.END

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """
        Cancels and ends the conversation
        """
        user = update.message.from_user
        logging.info("User %s canceled the conversation", user.first_name)
        await update.message.reply_text(
            "Conversation canceled, see you later!", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END
