
from tornado.gen import coroutine, Return
import tornado.ioloop
from common.model import Model

import logging
import os
import server
import common.events
import random
import datetime


class GameServersModel(Model):

    DEPLOYMENTS = "deployments"
    RUNTIME = "runtime"

    def __init__(self, app,
                 sock_path="/tmp",
                 binaries_path="/opt/gs",
                 ports_pool_from=2000,
                 ports_pool_to=3000):

        self.app = app
        self.sock_path = sock_path
        self.binaries_path = binaries_path

        if not os.path.isdir(self.binaries_path):
            os.mkdir(self.binaries_path)

        self.pool = PortsPool(ports_pool_from, ports_pool_to)
        self.servers = {}
        self.servers_rooms = {}
        self.sub = common.events.Subscriber(self)
        self.pub = common.events.Publisher()

    def get_server(self, name):
        return self.servers.get(name, None)

    def get_server_by_room(self, room_id):
        return self.servers_rooms.get(room_id)

    def get_servers(self):
        return self.servers

    def search(self, logs=None):

        result = {}

        for server_name, instance in self.get_servers().iteritems():
            if logs and instance.has_log(logs):
                result[server_name] = instance
                continue

            pass

        return result

    @coroutine
    def instantiate(self, name, game_id, game_version, game_server_name, deployment, room):
        gs = server.GameServer(self, game_id, game_version, game_server_name, deployment, name, room)
        self.servers[name] = gs
        self.servers_rooms[room.id()] = gs

        self.sub.subscribe(gs.pub, ["server_updated"])
        self.pub.notify("new_server", server=gs)

        raise Return(gs)

    @coroutine
    def server_updated(self, server):
        self.pub.notify("server_updated", server=server)

    @coroutine
    def spawn(self, game_name, game_version, game_server_name, deployment, room):
        name = game_name + "_" + game_server_name + "_" + str(room.id())

        game_settings = room.game_settings()

        try:
            binary = game_settings["binary"]
            arguments = game_settings["arguments"]
        except (KeyError, ValueError) as e:
            raise server.SpawnError("Failed to spawn game server: " + e.message)

        env = {
            e["key"]: e["value"]
            for e in game_settings.get("env", [])
            if "key" in e and "value" in e
        }

        instance = yield self.instantiate(name, game_name, game_version, game_server_name, deployment, room)

        app_path = os.path.join(self.binaries_path, GameServersModel.RUNTIME, game_name, game_version, deployment)
        
        sock_name = str(os.getpid()) + "_" + name
        sock_path = os.path.join(self.sock_path, sock_name)

        try:
            settings = yield instance.spawn(app_path, binary, sock_path, arguments, env, room)
        except server.SpawnError as e:
            logging.error("Failed to spawn server instance: " + e.message)
            import sys
            t, v, tb = sys.exc_info()
            yield instance.crashed("Failed to spawn server instance: " + e.message)
            raise t, v, tb

        logging.info("New server instance spawned: " + name)

        result = {
            "location": {
                "host": self.app.get_gs_host(),
                "ports": instance.ports
            },
            "settings": settings
        }

        raise Return(result)

    @coroutine
    def server_stopped(self, instance):
        self.sub.unsubscribe(instance.pub, ["server_updated"])
        self.pub.notify("server_removed", server=instance)

        def remove_server():
            s = self.servers.pop(instance.name, None)
            if s:
                self.servers_rooms.pop(s.room.id())

        tornado.ioloop.IOLoop.current().add_timeout(datetime.timedelta(minutes=10), remove_server)

    @coroutine
    def terminate_all(self, kill=False):
        yield [s.terminate(kill=kill) for name, s in self.servers.iteritems()]

    @coroutine
    def stopped(self):
        yield self.terminate_all(kill=True)


class PoolError(Exception):
    def __init__(self, message):
        self.message = message


class PortsPool(object):
    def __init__(self, port_from, port_to):
        self.ports = list(range(port_from, port_to))

    def acquire(self):

        if not self.ports:
            raise PoolError("No ports in pool left")

        try:
            return self.ports.pop(random.randrange(len(self.ports)))
        except KeyError:
            raise PoolError("No ports in pool left")

    def put(self, port):
        self.ports.append(port)
