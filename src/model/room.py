
from tornado.gen import coroutine, Return

import logging

from common.internal import Internal, InternalError
from common import retry


class NotifyError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message


class Room(object):
    def __init__(self, rooms, gamespace, room_id, settings):
        self.rooms = rooms
        self.slots = {}
        self.gamespace = gamespace
        self.settings = settings
        self.room_id = room_id
        self.internal = Internal()

        # special handles to support on special notify events
        self.notify_handlers = {}
        self.init_handlers()

        logging.info("New room created: " + str(room_id))

    def game_settings(self):
        return self.settings["game"]

    def id(self):
        return self.room_id

    @coroutine
    def notify(self, method, *args, **kwargs):
        """
        Notify the master server about actions, happened in the room
        """

        notify_handler = self.notify_handlers.get(method, None)

        # if there's a handler with such action name, call it first
        if notify_handler:
            result = yield notify_handler(*args, **kwargs)
            # and if it has some result, return it instead
            if result is not None:
                raise Return(result)

        try:
            @retry(operation="notify room {0} action {1}".format(self.id(), method), max=5, delay=10)
            def do_try(room_id, gamespace):
                return self.internal.request(
                    "game", "controller_action",
                    room_id=room_id,
                    action=method,
                    gamespace=gamespace,
                    args=args,
                    kwargs=kwargs)

            result = yield do_try(self.id(), self.gamespace)

        except InternalError as e:
            logging.error("Failed to notify an action: " + str(e.code) + ": " + e.body)

            raise NotifyError(e.code, e.message)
        else:
            raise Return(result)

    def room_settings(self):
        return self.settings["room"]

    def server_settings(self):
        return self.settings["server"]

    def other_settings(self):
        return self.settings.get("other", None)

    def add_handler(self, name, callback):
        self.notify_handlers[name] = callback

    def init_handlers(self):
        self.add_handler("update_settings", self.update_settings)

    # special notify handlers

    @coroutine
    def update_settings(self, settings, *args, **kwargs):
        if settings:
            self.room_settings().update(settings)

    @coroutine
    def release(self):
        # called when the room is being destroyed
        self.rooms.delete(self.room_id)


class RoomSlot(object):
    pass


class RoomsData(object):
    def __init__(self, application):
        self.rooms = {}
        self.application = application

    def delete(self, room_id):
        if room_id in self.rooms:
            logging.info("Room deleted: " + str(room_id))
            del self.rooms[room_id]

    def list(self):
        return self.rooms.iteritems()

    def get(self, room_id):
        return self.rooms.get(room_id)

    def new(self, gamespace, room_id, settings):
        logging.info("New room: " + str(room_id))

        room = Room(self, gamespace, room_id, settings)
        self.rooms[room_id] = room
        return room

