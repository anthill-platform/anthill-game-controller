
from tornado.gen import coroutine, Return

from common.internal import InternalError
from data.server import SpawnError


class InternalHandler(object):
    def __init__(self, application):
        self.application = application

    @coroutine
    def spawn(self, game_id, game_version, gamespace, room_id, settings):
        gs = self.application.gs
        rooms = self.application.rooms

        room = rooms.new(room_id, gamespace, settings)

        try:
            location = yield gs.spawn(game_id, game_version, room)
        except SpawnError as e:
            raise InternalError(500, "Failed to spawn: " + e.message)

        result = {
            "location": location
        }

        raise Return(result)
