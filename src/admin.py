
from tornado.gen import coroutine, Return
from tornado.ioloop import IOLoop

import common.admin as a
import common.events
import common.jsonrpc
import datetime


class DebugController(a.StreamAdminController):

    def __init__(self, app, token, handler):
        super(DebugController, self).__init__(app, token, handler)
        self.gs_controller = self.application.gs_controller
        self.sub = common.events.Subscriber(self)
        self._subscribed_to = set()
        self._logs_buffers = {}

    @coroutine
    def kill(self, server, hard):
        server = self.gs_controller.get_server_by_name(server)

        if not server:
            return

        yield server.terminate(kill=hard)

    @coroutine
    def log(self, name, data):
        yield self.send_rpc(self, "log", name=name, data=data)

    @coroutine
    def send_stdin(self, server, data):
        server = self.gs_controller.get_server_by_name(server)

        if not server:
            return

        yield server.send_stdin(data)

        raise Return({})

    @coroutine
    def new_server(self, server):
        yield self.send_rpc(self, "new_server", **DebugController.serialize_server(server))

    @coroutine
    def on_closed(self):
        del self.sub

    @coroutine
    def on_opened(self, *args, **kwargs):

        servers = self.gs_controller.list_servers_by_name()

        result = [DebugController.serialize_server(server) for server_name, server in servers.iteritems()]
        yield self.send_rpc(self, "servers", result)

        self.sub.subscribe(self.gs_controller.pub, ["new_server", "server_removed", "server_updated"])

    def scopes_stream(self):
        return ["game_admin"]

    @coroutine
    def search_logs(self, data):

        servers = self.gs_controller.search(logs=data)

        raise Return({
            "servers": [server_name for server_name, instance in servers.iteritems()]
        })

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

    @coroutine
    def server_removed(self, server):
        server.pub.unsubscribe(["log"], self)
        yield self.send_rpc(self, "server_removed", **DebugController.serialize_server(server))

    @coroutine
    def server_updated(self, server):
        yield self.send_rpc(self, "server_updated", **DebugController.serialize_server(server))

    @coroutine
    def subscribe_logs(self, server):
        server_instance = self.gs_controller.get_server_by_name(server)

        if not server_instance:
            raise common.jsonrpc.JsonRPCError(404, "No logs could be seen")

        if server in self._subscribed_to:
            raise common.jsonrpc.JsonRPCError(409, "Already subscribed")

        self._subscribed_to.add(server)
        IOLoop.current().add_callback(server_instance.stream_log, self.__read_log_stream__)
        raise Return({})

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

        log_buffer.append(unicode(line))
        return True

    def __flush_log_stream__(self, server_name):
        log_buffer = self._logs_buffers.get(server_name, None)

        if not log_buffer:
            return

        data = u"".join(log_buffer)

        IOLoop.current().add_callback(self.send_rpc, self, "log", name=server_name, data=data)

        self._logs_buffers.pop(server_name, None)

    @coroutine
    def usubscribe_logs(self, server):
        try:
            self._subscribed_to.remove(server)
        except KeyError:
            pass

        self._logs_buffers.pop(server, None)
