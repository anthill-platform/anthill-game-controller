
import logging
from urllib import parse
import datetime

from anthill.common.model import Model
from anthill.common.access import AccessToken
from anthill.common import retry
from anthill.common import discover
from anthill.common.jsonrpc import JsonRPC, JsonRPCError, JSONRPC_TIMEOUT

from tornado.websocket import websocket_connect
from tornado.httpclient import AsyncHTTPClient, HTTPRequest, HTTPError
from tornado.ioloop import IOLoop
from tornado.gen import sleep

from . gameserver import SpawnError
from . delivery import DeliveryError


class MasterConnectionError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return str(self.code) + ": " + self.message


class MasterConnectionJsonRPC(JsonRPC):
    def __init__(self, websocket_connection):
        super(MasterConnectionJsonRPC, self).__init__()
        self.websocket_connection = websocket_connection
        self.close_expected = False

    def close(self):
        self.close_expected = True
        self.websocket_connection.close()

    async def write_data(self, context, data):
        self.websocket_connection.write_message(data)


class MasterConnectionModel(Model):
    def __init__(self, app, username, password, gamespace, region):
        self.app = app
        self.username = username
        self.password = password
        self.gamespace = gamespace
        self.region = region
        self.auth_location = None
        self.game_location = None
        self.session_token = None
        self.session_connection = None
        self.session_rpc = None
        self.postponed_messages = []
        self.relogin_timeout = None

    async def login(self):
        if self.relogin_timeout:
            IOLoop.current().remove_timeout(self.relogin_timeout)
            self.relogin_timeout = None

        http_client = AsyncHTTPClient()

        request = HTTPRequest(
            url=self.auth_location + "/auth",
            method="POST",
            body=parse.urlencode({
                "credential": "dev",
                "scopes": "game_host",
                "should_have": "game_host",
                "gamespace": self.gamespace,
                "username": self.username,
                "key": self.password,
                "as": "host_{0}".format(self.app.gs_host)
            }))

        try:
            response = await http_client.fetch(request)
        except HTTPError as e:
            if e.code == 403:
                raise MasterConnectionError(
                    403, "Please make sure you have correctly specified username and password, "
                         "and the account has 'game_host' scope of access.")
            raise MasterConnectionError(403, "Cannot login: " + str(e))
        else:
            self.session_token = AccessToken(response.body.decode())
            self.session_token.validate()
            if self.session_token.is_valid():
                self.relogin_timeout = IOLoop.current().add_timeout(
                    datetime.timedelta(seconds=self.session_token.time_left()), self.login)
            else:
                logging.warning("Token is not valid!")
                raise MasterConnectionError(403, "Obtained token is not valid")

    async def reconnect(self):
        self.session_rpc = None

        while True:
            logging.info("Lost connection to Game Master, reconnecting in 5s")
            await sleep(5)
            try:
                await self.login()
            except MasterConnectionError as e:
                logging.exception("Cannot re-login")
                continue
            else:
                break

        IOLoop.current().spawn_callback(self.session_connect)

    def session_message(self, message):
        if self.session_rpc is None:
            self.postponed_messages.append(message)
            return
        if message is None:
            IOLoop.current().spawn_callback(self.reconnect)
            return
        IOLoop.current().spawn_callback(self.session_rpc.received, self.session_connection, message)

    async def session_connect(self):
        logging.info("Starting a connection to Game Master ...")

        scheme, sep, rest = self.game_location.partition(':')

        schemes = {
            "http": "ws",
            "https": "wss"
        }

        if scheme not in schemes:
            raise Exception("Not supported scheme on child service: " + scheme)

        game_service_location = schemes[scheme] + ":" + rest

        try:
            self.session_connection = await websocket_connect(
                game_service_location + "/host?" + parse.urlencode({
                    "access_token": self.session_token.key,
                    "address": self.app.gs_host,
                    "region": self.region
                }), on_message_callback=self.session_message)
        except Exception as e:
            logging.exception("Cannot connect to game service")
            IOLoop.current().spawn_callback(self.reconnect)
            return

        logging.info("Connected to Game Master")
        self.session_rpc = MasterConnectionJsonRPC(self.session_connection)
        self.session_rpc.set_receive(self._on_message)

        if self.postponed_messages:
            for message in self.postponed_messages:
                self.session_message(message)
            self.postponed_messages = []

    async def _on_message(self, context, method, *args, **kwargs):
        full_name = "on_" + method + "_received"
        if hasattr(self, full_name):
            return await getattr(self, full_name)(*args, **kwargs)

    async def on_deploy_delivery_received(self, game_name, game_version, deployment_id, deployment_hash):
        try:
            return await self.download_deployment(game_name, game_version, deployment_id, deployment_hash)
        except Exception as e:
            raise JsonRPCError(500, str(e))

    async def download_deployment(self, game_name, game_version, deployment_id, deployment_hash=None):
        delivery = await self.app.delivery.deliver(game_name, game_version, deployment_id, deployment_hash)

        def streaming_callback(chunk):
            delivery.data_received(chunk)

        client = AsyncHTTPClient()

        try:
            request = HTTPRequest(url=self.game_location + "/deployment/{0}/{1}/{2}?access_token={3}".format(
                game_name, game_version, deployment_id, self.session_token.key
            ), streaming_callback=streaming_callback, request_timeout=600)
            await client.fetch(request)
        except HTTPError as e:
            raise Exception(str(e))
        else:
            try:
                await delivery.complete()
            except DeliveryError as e:
                raise Exception(str(e))
            return True

    async def on_delete_delivery_received(self, game_name, game_version, deployment_id):
        await self.app.delivery.delete(game_name, game_version, deployment_id)
        return True

    async def on_spawn_received(self, game_name, game_version,
                                game_server_name, room_id, deployment,
                                settings):
        try:
            return await self.app.gs_controller.spawn(
                room_id, settings, game_name,
                game_version, game_server_name, deployment)
        except SpawnError as e:
            raise JsonRPCError(500, e.message)

    async def on_debug_command_received(self, debug_command=None, **kwargs):
        """
        Websocket client sent a command to host controller
        websocket client -> host handler -> [host controller]
        """
        return await self.app.debug_controller.debug_command_received(debug_command=debug_command, **kwargs)

    async def _session_rpc_callback(self, session_id, debug_action, **kwargs):
        if self.session_rpc is None:
            return
        await self.session_rpc.send_rpc(
            self, "session_debug_action", session_id=session_id, debug_action=debug_action, **kwargs)

    async def _global_rpc_callback(self, debug_action, **kwargs):
        if self.session_rpc is None:
            return
        await self.session_rpc.send_rpc(
            self, "global_debug_action", debug_action=debug_action, **kwargs)

    async def notify(self, room_id, method, *args, **kwargs):
        if self.session_rpc is None:
            raise JsonRPCError(500, "No RPC session available")

        return await self.session_rpc.send_request(
            self, "notify", JSONRPC_TIMEOUT,
            notify_action=method, room_id=room_id, args=args, kwargs=kwargs)

    async def on_debug_open_received(self, *args, **kwargs):
        return await self.app.debug_controller.command_open(self._session_rpc_callback)

    async def on_debug_close_received(self, session_id, *args, **kwargs):
        await self.app.debug_controller.command_close(session_id)

    async def on_terminate_room_received(self, room_id, *args, **kwargs):
        gs_controller = self.app.gs_controller
        s = gs_controller.get_server_by_room(room_id)

        if not s:
            raise HTTPError(404, "No such server")

        await s.terminate()

    async def on_execute_stdin_received(self, room_id, command, *args, **kwargs):
        gs_controller = self.app.gs_controller
        s = gs_controller.get_server_by_room(room_id)

        if not s:
            raise HTTPError(404, "No such server")

        await s.send_stdin(command)

    async def on_shutdown_received(self, *args, **kwargs):
        logging.info("Shutdown command received")
        IOLoop.current().stop()

    async def on_heartbeat_received(self, *args, **kwargs):
        return self.app.heartbeat.report()

    async def started(self, application):

        application.debug_controller.set_rpc_callback(self._global_rpc_callback)

        @retry(operation="locate auth external", max=5, delay=5)
        async def locate_auth():
            return await discover.cache.get_service_external("login")

        @retry(operation="locate game external", max=5, delay=5)
        async def locate_game():
            return await discover.cache.get_service_external("game")

        logging.info("Locating auth service")
        self.auth_location = await locate_auth()
        logging.info("Locating game service")
        self.game_location = await locate_game()
        logging.info("Located: {0}, logging in ...".format(self.auth_location))
        await self.login()
        logging.info("Logged in")
        IOLoop.current().spawn_callback(self.session_connect)
        await super().started(application)
