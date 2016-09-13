
from tornado.gen import coroutine, Return

import common.admin as a
import common.events
import common.jsonrpc


class DebugController(a.StreamAdminController):

    def __init__(self, app, token, handler):
        super(DebugController, self).__init__(app, token, handler)
        self.gs = self.application.gs
        self.sub = common.events.Subscriber(self)

    @coroutine
    def kill(self, server, hard):
        server = self.gs.get_server(server)

        if not server:
            return

        yield server.terminate(kill=hard)

    @coroutine
    def log(self, name, data):
        yield self.rpc(self, "log", name=name, data=data)

    @coroutine
    def new_server(self, server):
        yield self.rpc(self, "new_server", **DebugController.serialize_server(server))

    def on_close(self):
        self.sub.unsubscribe_all()

    @coroutine
    def opened(self, **kwargs):

        servers = self.gs.get_servers()

        result = [DebugController.serialize_server(server) for server_name, server in servers.iteritems()]
        yield self.rpc(self, "servers", result)

        self.sub.subscribe(self.gs.pub, ["new_server", "server_removed", "server_status"])

    def scopes_stream(self):
        return ["game_admin"]

    @coroutine
    def search_logs(self, data):
        servers = self.gs.get_servers()

        result = [
            server_name
            for server_name, server in servers.iteritems()
            if data in server.get_log()
        ]

        raise Return({
            "servers": result
        })

    @staticmethod
    def serialize_server(server):
        return {
            "status": server.status,
            "game": server.game_id,
            "version": server.game_version,
            "name": server.name
        }

    @coroutine
    def server_removed(self, server):
        server.pub.unsubscribe(["log"], self)
        yield self.rpc(self, "server_removed", **DebugController.serialize_server(server))

    @coroutine
    def server_status(self, name, status):
        yield self.rpc(self, "server_status", name=name, status=status)

    @coroutine
    def subscribe_logs(self, server):
        server = self.gs.get_server(server)

        if not server:
            raise common.jsonrpc.JsonRPCError(404, "No logs could be seen")

        # get the logs already available
        logs = server.get_log()

        # subscribe for the additional logs
        self.sub.subscribe(server.pub, ["log"])

        raise Return({
            "stream": logs
        })

    @coroutine
    def usubscribe_logs(self, server):

        server = self.gs.get_server(server)

        if not server:
            raise common.jsonrpc.JsonRPCError(404, "No such server")

        # unsubscribe from the logs (if we are)
        self.sub.unsubscribe(server.pub, ["log"])

