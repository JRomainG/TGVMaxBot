import abc
import json
import logging
import requests
from typing import List
from datetime import datetime, timedelta
from urllib.parse import quote

from telegram.ext import Job, Application, ContextTypes


class Trip:
    def __init__(
        self,
        origin: str,
        destination: str,
        min_date: datetime,
        max_date: datetime,
        max_duration: timedelta,
        job: Job = None,
    ):
        self.origin = origin
        self.destination = destination
        self.min_date = min_date
        self.max_date = max_date
        self.max_duration = max_duration
        self.job = job

    @staticmethod
    def parse_date(date: str) -> datetime:
        try:
            return datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    @staticmethod
    def parse_duration(duration: str) -> timedelta:
        try:
            h, m = map(int, duration.split(":"))
            return timedelta(hours=h, minutes=m)
        except ValueError:
            return None

    @staticmethod
    def from_config(config: dict) -> "Trip":
        return Trip(
            config["origin"],
            config["destination"],
            config["min_date"],
            config["max_date"],
            config["max_duration"],
        )

    def __str__(self) -> str:
        return "Trip from {} to {} (departure between {} and {}) with a max duration of {}".format(
            self.origin,
            self.destination,
            self.min_date.strftime("%Y-%m-%d %H:%M:%S"),
            self.max_date.strftime("%Y-%m-%d %H:%M:%S"),
            self.max_duration,
        )


class Ticket:
    def __init__(
        self,
        origin: str,
        destination: str,
        departure_time: datetime,
        arrival_time: datetime,
        transporter: str,
        train_id: str,
        available_seats: int,
    ):
        self.origin = origin
        self.destination = destination
        self.departure_time = departure_time
        self.arrival_time = arrival_time
        self.transporter = transporter
        self.train_id = train_id
        self.available_seats = available_seats

    @staticmethod
    def from_json(data: dict) -> "Ticket":
        return Ticket(
            origin=data["originName"],
            destination=data["destinationName"],
            departure_time=datetime.strptime(
                data["departureDateTime"], "%Y-%m-%dT%H:%M:%S"
            ),
            arrival_time=datetime.strptime(
                data["arrivalDateTime"], "%Y-%m-%dT%H:%M:%S"
            ),
            transporter=data["axe"],
            train_id=data["train"],
            available_seats=data["availableSeatsCount"],
        )

    def __str__(self) -> str:
        return "Ticket from {} to {}, {} - {} ({}), {} seats available".format(
            self.origin,
            self.destination,
            self.departure_time.strftime("%Y-%m-%d %H:%M:%S"),
            self.arrival_time.strftime("%Y-%m-%d %H:%M:%S"),
            self.transporter,
            self.available_seats,
        )

    def __eq__(self, obj):
        return (
            isinstance(obj, Ticket)
            and self.train_id == obj.train_id
            and self.origin == obj.origin
            and self.destination == obj.destination
            and self.transporter == obj.transporter
            and self.departure_time == obj.departure_time
            and self.arrival_time == obj.arrival_time
        )


class GenericBackend(abc.ABC):
    """
    Abstract class for backends
    """

    USER_AGENT_STRING = (
        "Mozilla/5.0 (Windows NT 6.1; Win64; x64) Gecko/20100101 Firefox/81.0"
    )

    def __init__(self, application: Application, check_interval: float):
        self.trips = {}
        self.application = application
        self.check_interval = check_interval

    def _get(self, url: str, params: dict = {}) -> str:
        res = requests.get(
            url=url, params=params, headers={"User-Agent": self.USER_AGENT_STRING}
        )
        return res.text

    def _post(self, url: str, data: dict) -> str:
        res = requests.post(
            url=url, json=data, headers={"User-Agent": self.USER_AGENT_STRING}
        )
        return res.text

    def add_trip(self, context: ContextTypes.DEFAULT_TYPE, trip: Trip):
        user_id = context._user_id
        chat_id = context._chat_id
        trip.job = self.application.job_queue.run_repeating(
            self.check_trip,
            interval=self.check_interval,
            first=1,
            chat_id=chat_id,
            user_id=user_id,
            name=f"{chat_id}.{user_id}",
            data=trip,
        )
        if user_id not in self.trips:
            self.trips[user_id] = []
        self.trips[user_id].append(trip)
        logging.info("Created %s for user %d", trip, user_id)

    def get_trips(self, context: ContextTypes.DEFAULT_TYPE):
        return self.trips[context._user_id]

    def get_trip(self, context: ContextTypes.DEFAULT_TYPE, trip_id: int):
        return self.trips[context._user_id][trip_id]

    def remove_trip(self, context: ContextTypes.DEFAULT_TYPE, trip_id: int):
        trip = self.get_trip(context, trip_id)
        trip.job.enabled = False
        trip.job.schedule_removal()
        del self.trips[context._user_id][trip_id]
        logging.info("Removed %s for user %d", trip, context._user_id)

    async def check_trip(self, context: ContextTypes.DEFAULT_TYPE):
        user_id = context._user_id
        trip = context.job.data

        if trip.max_date < datetime.now():
            logging.debug("Found expired trip %s", trip)
            self.remove_trip(context, self.trips[user_id].index(trip))
            return

        logging.debug("Checking %s for user %d", trip, context._user_id)
        await self._check_trip(context, trip)

    @abc.abstractmethod
    async def _check_trip(self, user_id: int, trip: Trip):
        raise NotImplementedError()

    def restore(self, state):
        raise NotImplementedError()


class TGVBot(GenericBackend):
    URL_FORMAT = "https://sncf-simulateur-api-prod.azurewebsites.net/api/RailAvailability/Search/{origin}/{destination}/{min_date}/{max_date}"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._notified_tickets = {}
        self._silent = kwargs.get("silent", False)

    def get_available_tickets(self, trip: Trip):
        try:
            url = self.URL_FORMAT.format(
                origin=quote(trip.origin),
                destination=quote(trip.destination),
                min_date=trip.min_date.strftime("%Y-%m-%dT%H:%M:%S"),
                max_date=trip.max_date.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            logging.debug("[TGVBot] Fetching tickets from: %s", url)
            data = json.loads(self._get(url))
            return [Ticket.from_json(t) for t in data]
        except Exception as e:
            logging.warning("[TGVBot] Failed to fetch available tickets: %s", e)
            return []

    async def notify_tickets(
        self, context: ContextTypes.DEFAULT_TYPE, trip: Trip, tickets: List[Ticket]
    ):
        if not tickets:
            return
        msg = f"Found {len(tickets)} new option(s) for {trip}:\n"
        msg += "\n".join([f"- {t}" for t in tickets])
        await context.bot.send_message(
            context.job.chat_id,
            text=msg,
            disable_web_page_preview=True,
            disable_notification=self._silent,
        )

    def _update_notified_tickets(self, user_id: int, tickets: List[Ticket]):
        if user_id not in self._notified_tickets:
            self._notified_tickets[user_id] = []
            return

        for ticket in tickets:
            if ticket.available_seats == 0:
                # If a ticket has no available seats but was notified in the
                # past, we want to get a new notification when a seat is
                # available again, so remove it from _notified_tickets
                try:
                    self._notified_tickets[user_id].remove(ticket)
                except ValueError:
                    pass
            elif ticket not in self._notified_tickets[user_id]:
                self._notified_tickets[user_id].append(ticket)

        # We don't want the list of notified tickets to grow too large, so
        # remove those with a departure date in the past
        for ticket in self._notified_tickets[user_id]:
            if ticket.departure_time < datetime.now():
                self._notified_tickets[user_id].remove(ticket)

    async def _check_trip(self, context: ContextTypes.DEFAULT_TYPE, trip: Trip):
        tickets = self.get_available_tickets(trip)
        available_tickets = [t for t in tickets if t.available_seats > 0]
        logging.debug(
            "[TGVBot] Found %d ticket(s) (%d with available seats) for %s",
            len(tickets),
            len(available_tickets),
            trip,
        )

        tickets_to_notify = []
        for ticket in available_tickets:
            if ticket.departure_time < trip.min_date:
                continue
            if ticket.departure_time > trip.max_date:
                continue
            if ticket.arrival_time - ticket.departure_time > trip.max_duration:
                continue

            logging.debug("[TGVBot] Found %s matching user requirements", ticket)

            # Don't notify for tickets twice
            if ticket not in self._notified_tickets.get(context._user_id, []):
                tickets_to_notify.append(ticket)

        logging.debug("[TGVBot] Found %d new ticket(s)", len(tickets_to_notify))
        await self.notify_tickets(context, trip, tickets_to_notify)
        self._update_notified_tickets(context._user_id, tickets)
