
from tornado.gen import coroutine, Return
from tornado.web import HTTPError, stream_request_body

from common.internal import InternalError
from common.access import internal
from common.handler import AuthenticatedHandler

from model.gameserver import SpawnError
from model.delivery import DeliveryError

import ujson


class InternalHandler(object):
    def __init__(self, application):
        self.application = application


class DeploymentHandler(AuthenticatedHandler):
    @coroutine
    @internal
    def delete(self, game_name, game_version, deployment_id):
        delivery = self.application.delivery

        try:
            yield delivery.delete(game_name, game_version, deployment_id)
        except DeliveryError as e:
            raise HTTPError(e.code, e.message)


@stream_request_body
class DeliverDeploymentHandler(AuthenticatedHandler):
    def __init__(self, application, request, **kwargs):
        super(DeliverDeploymentHandler, self).__init__(application, request, **kwargs)
        self.delivery = None

    @coroutine
    @internal
    def put(self, *args, **kwargs):
        try:
            yield self.delivery.complete()
        except DeliveryError as e:
            raise HTTPError(e.code, e.message)

    @coroutine
    def data_received(self, chunk):
        yield self.delivery.data_received(chunk)

    @coroutine
    def prepare(self):
        self.request.connection.set_max_body_size(1073741824)
        yield super(DeliverDeploymentHandler, self).prepare()

    @coroutine
    @internal
    def prepared(self, game_name, game_version, deployment_id, *args, **kwargs):
        deployment_hash = self.get_argument("deployment_hash")

        delivery = self.application.delivery

        try:
            self.delivery = yield delivery.deliver(
                game_name, game_version, deployment_id, deployment_hash)
        except DeliveryError as e:
            raise HTTPError(e.code, e.message)


class SpawnHandler(AuthenticatedHandler):
    @coroutine
    @internal
    def post(self):

        game_name = self.get_argument("game_id")
        game_version = self.get_argument("game_version")
        game_server_name = self.get_argument("game_server_name")
        gamespace = self.get_argument("gamespace")
        room_id = self.get_argument("room_id")
        deployment = self.get_argument("deployment")

        try:
            settings = ujson.loads(self.get_argument("settings"))
        except (KeyError, ValueError):
            raise HTTPError(400, "Corrupted settings")

        gs_controller = self.application.gs_controller

        try:
            result = yield gs_controller.spawn(
                gamespace, room_id, settings, game_name,
                game_version, game_server_name, deployment)
        except SpawnError as e:
            raise HTTPError(500, "Failed to spawn: " + e.message)

        self.dumps(result)


class TerminateHandler(AuthenticatedHandler):
    @coroutine
    @internal
    def post(self):
        room_id = self.get_argument("room_id")

        gs_controller = self.application.gs_controller
        s = gs_controller.get_server_by_room(room_id)

        if not s:
            raise HTTPError(404, "No such server")

        yield s.terminate()


class ExecuteStdInHandler(AuthenticatedHandler):
    @coroutine
    @internal
    def post(self):
        room_id = self.get_argument("room_id")
        command = self.get_argument("command")

        gs_controller = self.application.gs_controller
        s = gs_controller.get_server_by_room(room_id)

        if not s:
            raise HTTPError(404, "No such server")

        yield s.send_stdin(command)


class HeartbeatHandler(AuthenticatedHandler):
    @internal
    def get(self):
        report = self.application.heartbeat.report()
        self.dumps(report)
