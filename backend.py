import abc
import json
import logging
import requests
from typing import List
from datetime import datetime
from urllib.parse import quote

from telegram.ext import Job, Application, ContextTypes, CallbackContext


class Trip:
    def __init__(
        self,
        origin: str,
        destination: str,
        min_date: datetime,
        max_date: datetime,
        job: Job = None,
    ):
        self.origin = origin
        self.destination = destination
        self.min_date = min_date
        self.max_date = max_date
        self.job = job

    @staticmethod
    def parse_date(date: str) -> datetime:
        try:
            return datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def __str__(self) -> str:
        return "Trip from {} to {} (departure between {} and {})".format(
            self.origin,
            self.destination,
            self.min_date.strftime("%Y-%m-%d %H:%M:%S"),
            self.max_date.strftime("%Y-%m-%d %H:%M:%S"),
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
        self.min_date = departure_time
        self.max_date = arrival_time
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
            self.min_date.strftime("%Y-%m-%d %H:%M:%S"),
            self.max_date.strftime("%Y-%m-%d %H:%M:%S"),
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
            first=10,
            chat_id=chat_id,
            user_id=user_id,
            name=f"{chat_id}-{user_id}",
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

    async def check_trip(self, context: CallbackContext):
        logging.debug("Checking %s for user %d", context.job.data, context._user_id)
        await self._check_trip(context._user_id, context.job.data)

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
            logging.warn("[TGVBot] Failed to fetch available tickets: %s", e)
            return []

    def notify_trip(self, ticket: Ticket):
        message = "TODO"
        self.transport.send_message(message)

    def _update_notified_tickets(self, tickets: List[Ticket]):
        for ticket in tickets:
            if ticket.available_seats == 0:
                # If a ticket has no available seats but was notified in the
                # past, we want to get a new notification when a seat is
                # available again, so remove it from _notified_tickets
                try:
                    self._notified_tickets.remove(ticket)
                except ValueError:
                    pass
            elif ticket not in self._notified_tickets:
                self._notified_tickets.append(ticket)

        # We don't want the list of notified tickets to grow too large, so
        # remove those with a departure date in the past
        for ticket in self._notified_tickets:
            if ticket.departure_time < datetime.now():
                self._notified_tickets.remove(ticket)

    async def _check_trip(self, user_id: int, trip: Trip):
        # TODO
        return

        tickets = self.get_available_tickets()
        available_tickets = [t for t in tickets if t.available_seats > 0]
        logging.debug(
            "[TGVBot] Found %d ticket(s) (%d with available seats) for %s: %s",
            len(tickets),
            len(available_tickets),
            self.trip,
        )

        for ticket in available_tickets:
            # Don't notify for tickets twice
            if ticket not in self._notified_tickets:
                self.notify_ticket(ticket)

        self._update_notified_tickets(tickets)
