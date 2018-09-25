
from tornado.ioloop import IOLoop

import anthill.common.admin as a
from anthill.common import events, jsonrpc
import datetime


class DebugController(a.StreamAdminController):

    def __init__(self, app, token, handler):
        super(DebugController, self).__init__(app, token, handler)
        self.gs_controller = self.application.gs_controller
        self.sub = events.Subscriber(self)
        self._subscribed_to = set()
        self._logs_buffers = {}

    async def kill(self, server, hard):
        server = self.gs_controller.get_server_by_name(server)

        if not server:
            return

        await server.terminate(kill=hard)

    async def send_stdin(self, server, data):
        server = self.gs_controller.get_server_by_name(server)

        if not server:
            return

        await server.send_stdin(data)

        return {}

    async def new_server(self, server):
        await self.send_rpc(self, "new_server", **DebugController.serialize_server(server))

    async def on_closed(self):
        self._subscribed_to = set()
        self.sub.unsubscribe_all()
        del self.sub

    async def on_opened(self, *args, **kwargs):

        servers = self.gs_controller.list_servers_by_name()

        result = [DebugController.serialize_server(server) for server_name, server in servers.items()]
        await self.send_rpc(self, "servers", result)

        self.sub.subscribe(self.gs_controller.pub, ["new_server", "server_removed", "server_updated"])

    def scopes_stream(self):
        return ["game_admin"]

    async def search_logs(self, data):

        servers = self.gs_controller.search(logs=data)

        return {
            "servers": [server_name for server_name, instance in servers.items()]
        }

    @staticmethod
    def serialize_server(server):
        return {
            "status": server.status,
            "game": server.game_name,
            "room_settings": server.room.room_settings(),
            "version": server.game_version,
            "deployment": server.deployment,
            "name": server.name,
            "room_id": server.room.id()
        }

    async def server_removed(self, server):
        await self.send_rpc(self, "server_removed", **DebugController.serialize_server(server))

    async def server_updated(self, server):
        await self.send_rpc(self, "server_updated", **DebugController.serialize_server(server))

    async def subscribe_logs(self, server):
        server_instance = self.gs_controller.get_server_by_name(server)

        if not server_instance:
            raise jsonrpc.JsonRPCError(404, "No logs could be seen")

        if server in self._subscribed_to:
            raise jsonrpc.JsonRPCError(409, "Already subscribed")

        self._subscribed_to.add(server)
        IOLoop.current().add_callback(server_instance.stream_log, self.__read_log_stream__)
        return {}

    def __read_log_stream__(self, server_name, line):

        subscribed = server_name in self._subscribed_to

        if not subscribed:
            # once 'usubscribe_logs' is called, this one will return False, thus stopping the 'stream_log'
            return False

        log_buffer = self._logs_buffers.get(server_name, None)

        if log_buffer is None:
            log_buffer = []
            self._logs_buffers[server_name] = log_buffer
            IOLoop.current().add_timeout(datetime.timedelta(seconds=2), self.__flush_log_stream__, server_name)

        log_buffer.append(str(line))
        return True

    def __flush_log_stream__(self, server_name):
        log_buffer = self._logs_buffers.get(server_name, None)

        if not log_buffer:
            return

        data = u"".join(log_buffer)

        IOLoop.current().add_callback(self.send_rpc, self, "log", name=server_name, data=data)

        self._logs_buffers.pop(server_name, None)

    async def usubscribe_logs(self, server):
        try:
            self._subscribed_to.remove(server)
        except KeyError:
            pass

        self._logs_buffers.pop(server, None)
