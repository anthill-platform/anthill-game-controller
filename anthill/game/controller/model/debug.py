
from tornado.ioloop import IOLoop

from anthill.common import events, jsonrpc
from anthill.common.model import Model
import datetime
import uuid


class DebugControllerSession(object):
    def __init__(self, app, session_id, rpc_callback):
        self.app = app
        self.session_id = session_id
        self.rpc_callback = rpc_callback
        self.sub = events.Subscriber(self)
        self._subscribed_to = set()
        self._logs_buffers = {}

    async def kill(self, server, hard):
        server = self.app.gs_controller.get_server_by_name(server)

        if not server:
            return False
        await server.terminate(kill=hard)

        return True

    async def send_stdin(self, server, data):
        server = self.app.gs_controller.get_server_by_name(server)
        if not server:
            return

        await server.send_stdin(data)
        return True

    async def send_rpc(self, debug_action, **kwargs):
        if self.rpc_callback is None:
            return
        await self.rpc_callback(self.session_id, debug_action, **kwargs)

    async def close(self):
        self._subscribed_to = set()
        self.sub.unsubscribe_all()
        self.rpc_callback = None
        del self.sub

    async def command_received(self, action, *args, **kwargs):
        if hasattr(self, action):
            if action.startswith("_"):
                raise jsonrpc.JsonRPCError(400, "Actions starting with underscore are not allowed!")
            try:
                response = await getattr(self, action)(*args, **kwargs)
            except TypeError as e:
                raise jsonrpc.JsonRPCError(400, "Bad arguments: " + e.args[0])
            except Exception as e:
                raise jsonrpc.JsonRPCError(500, "Error: " + str(e))
            return response

    async def get_servers(self):
        servers = self.app.gs_controller.list_servers_by_name()
        return [DebugControllerSession.serialize_server(server) for server_name, server in servers.items()]

    async def search_logs(self, data):

        servers = self.app.gs_controller.search(logs=data)

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

    async def subscribe_logs(self, server):
        server_instance = self.app.gs_controller.get_server_by_name(server)

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

        IOLoop.current().add_callback(self.send_rpc, "log", name=server_name, data=data)

        self._logs_buffers.pop(server_name, None)

    async def usubscribe_logs(self, server):
        try:
            self._subscribed_to.remove(server)
        except KeyError:
            pass

        self._logs_buffers.pop(server, None)


class DebugControllerModel(Model):
    next_session_id = 0

    def __init__(self, app):
        self.app = app
        self.sub = events.Subscriber(self)
        self.sessions = {}
        self.rpc_callback = None

    def set_rpc_callback(self, rpc_callback):
        self.rpc_callback = rpc_callback

    async def command_close(self, session_id):
        sub = self.sessions.get(session_id)
        if sub:
            await sub.close()
            self.sessions.pop(session_id)

    async def debug_command_received(self, session_id, debug_command, **kwargs):
        sub = self.sessions.get(session_id)
        if sub is None:
            return

        if hasattr(sub, debug_command):
            if debug_command.startswith("_"):
                raise jsonrpc.JsonRPCError(400, "Actions starting with underscore are not allowed!")
            try:
                return await getattr(sub, debug_command)(**kwargs)
            except TypeError as e:
                raise jsonrpc.JsonRPCError(400, "Bad arguments: " + e.args[0])
            except Exception as e:
                raise jsonrpc.JsonRPCError(500, "Error: " + str(e))

    async def started(self, application):
        self.sub.subscribe(self.app.gs_controller.pub, ["new_server", "server_removed", "server_updated"])
        await super().started(application)

    async def send_rpc(self, debug_action, **kwargs):
        if self.rpc_callback is None:
            return
        await self.rpc_callback(debug_action, **kwargs)

    async def new_server(self, server):
        await self.send_rpc("new_server", **DebugControllerSession.serialize_server(server))

    async def server_removed(self, server):
        await self.send_rpc("server_removed", **DebugControllerSession.serialize_server(server))

    async def server_updated(self, server):
        await self.send_rpc("server_updated", **DebugControllerSession.serialize_server(server))

    async def command_open(self, rpc_callback):
        session_id = str(uuid.uuid4())
        session = DebugControllerSession(self.app, session_id, rpc_callback)
        self.sessions[session_id] = session
        return session_id
