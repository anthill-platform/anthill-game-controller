
from tornado.ioloop import PeriodicCallback
from tornado.gen import multi
from tornado.ioloop import IOLoop
from tornado.httpclient import HTTPRequest

from anthill.common.model import Model
from anthill.common.server import Server
from anthill.common.internal import Internal, InternalError
from anthill.common import retry, events, ElapsedTime

from . import gameserver

import logging
import os
import random
import datetime
import time
import socket
from contextlib import closing

from . import unix_domain_sockets_enabled


class Room(object):
    """
    An object that represents a single game room, and keeps these settings:
       1) game_settings, like binary name or the ports count, game_settings()
       2) room_settings, an abstract object the players can define and filter upon it, like current map or gaming mode
       3) server_settings, custom object defined by admins, can be redefined for each game version separately
    """

    def __init__(self, room_id, settings):
        self.settings = settings
        self.room_id = str(room_id)
        self.internal = Internal()

        # special handles to support on special notify events
        self.notify_handlers = {}

        self.init_handlers()

        logging.info("New room created: " + str(room_id))

    def game_settings(self):
        """
        :return: game-related settings, like binary name or the ports count, game_settings()
        """
        return self.settings["game"]

    def room_settings(self):
        """
        :return: an abstract object the players can define and filter upon it, like current map or gaming mode
        """
        return self.settings["room"]

    def server_settings(self):
        """
        :return: custom object defined by admins, can be redefined for each game version separately
        """
        return self.settings["server"]

    def other_settings(self):
        """
        :return: other possible settings, for example the ones defined by admins or party-related settings
        """
        return self.settings.get("other", None)

    def id(self):
        return self.room_id

    @staticmethod
    def __notify_retry_predicate__(e):
        if isinstance(e, InternalError):
            # on 4XX codes do not retry and error out immediately
            return e.code < 500
        return False

    async def notify(self, method, *args, **kwargs):
        """
        Notify the master server about actions, happened in the room
        """

        notify_handler = self.notify_handlers.get(method, None) if self.notify_handlers else None

        # if there's a handler with such action name, call it first
        if notify_handler:
            result = await notify_handler(*args, **kwargs)
            # and if it has some result, return it instead
            if result is not None:
                return result

        try:
            @retry(operation="notify room {0} action {1}".format(self.id(), method), max=5, delay=10,
                   predicate=Room.__notify_retry_predicate__)
            async def do_try(room_id):
                timer = ElapsedTime("controller notify {0}".format(method))
                try:
                    return await Server.instance().master.notify(room_id, method, *args, **kwargs)
                finally:
                    logging.info(timer.done())

            result = await do_try(self.id())

        except InternalError as e:
            logging.error("Failed to notify an action: " + str(e.code) + ": " + e.body)

            raise gameserver.NotifyError(e.code, str(e))
        else:
            return result

    def add_handler(self, name, callback):
        self.notify_handlers[name] = callback

    def init_handlers(self):
        self.add_handler("update_settings", self.update_settings)

    # special notify handlers

    async def update_settings(self, settings, *args, **kwargs):
        if settings:
            self.room_settings().update(settings)

    def dispose(self):
        self.notify_handlers = None

    def __del__(self):
        logging.info("Room instance has been deleted: " + self.room_id)


class GameServersControllerModel(Model):

    DEPLOYMENTS = "deployments"
    RUNTIME = "runtime"

    def __init__(self, app, sock_path, binaries_path,
                 logs_path, logs_keep_time, logs_max_file_size,
                 ports_pool_from, ports_pool_to):

        self.app = app
        self.sock_path = sock_path
        self.binaries_path = binaries_path
        self.logs_path = logs_path
        self.logs_keep_time = logs_keep_time
        self.logs_max_file_size = logs_max_file_size

        if not os.path.isdir(self.binaries_path):
            os.makedirs(self.binaries_path, exist_ok=True)

        if not os.path.isdir(self.logs_path):
            os.makedirs(self.logs_path, exist_ok=True)

        self.clear_logs_cb = PeriodicCallback(self.__clear_logs__, 300000)

        self.pool = PortsPool(ports_pool_from, ports_pool_to)
        self.sub = events.Subscriber(self)
        self.pub = events.Publisher()

        self.servers_by_name = {}
        self.servers_by_room_id = {}
        self.rooms = {}

        self.__clear_logs__()

    def __clear_logs__(self):
        now = time.time()

        removed_counter = 0

        for f in os.listdir(self.logs_path):
            full_path = os.path.join(self.logs_path, f)
            try:
                if os.stat(full_path).st_mtime < now - self.logs_keep_time:
                    os.remove(full_path)
                    removed_counter += 1
            except OSError:
                pass

        if removed_counter > 0:
            logging.info("Removed {0} old logging files".format(removed_counter))

    def get_server_by_name(self, name):
        return self.servers_by_name.get(name, None)

    def get_server_by_room(self, room_id):
        return self.servers_by_room_id.get(room_id)

    def list_servers_by_name(self):
        return self.servers_by_name

    def search(self, logs=None):

        result = {}

        for server_name, instance in self.servers_by_name.items():
            if logs and instance.log_contains_text(logs):
                result[server_name] = instance
                continue

            pass

        return result

    def delete_room(self, room_id):
        room = self.rooms.pop(room_id, None)
        if room:
            logging.info("Room deleted: " + str(room_id))
            room.dispose()

    def list_rooms(self):
        """
        :returns: An iterable [room_id, room] of currently active rooms
        """
        return self.rooms.items()

    def get_room(self, room_id):
        """
        :return: Currently active room by it's id, if any
        """
        return self.rooms.get(room_id, None)

    def create_new_room(self, room_id, settings):
        logging.info("New room: " + str(room_id))

        room = Room(room_id, settings)
        self.rooms[room_id] = room
        return room

    async def instantiate(self, name, game_id, game_version, game_server_name, deployment, room):

        log_file_path = os.path.join(self.logs_path, name + ".log")

        gs = gameserver.GameServer(
            self, game_id, game_version, game_server_name,
            deployment, name, room, log_file_path, self.logs_max_file_size)

        self.servers_by_name[name] = gs
        self.servers_by_room_id[room.id()] = gs

        self.sub.subscribe(gs.pub, ["server_updated"])
        self.pub.notify("new_server", server=gs)

        return gs

    async def server_updated(self, server):
        self.pub.notify("server_updated", server=server)

    async def spawn(self, room_id, settings, game_name, game_version, game_server_name, deployment):
        name = game_name + "_" + game_server_name + "_" + str(room_id)

        # register a new room
        room = self.create_new_room(room_id, settings)

        game_settings = room.game_settings()

        binary_option = "binary_win" if os.name == "nt" else "binary"

        try:
            binary = game_settings[binary_option]
            arguments = game_settings["arguments"]
        except (KeyError, ValueError) as e:
            raise gameserver.SpawnError("Failed to spawn game server, missing option: {0}".format(e))

        env = {
            e["key"]: e["value"]
            for e in game_settings.get("env", [])
            if "key" in e and "value" in e
        }

        instance = await self.instantiate(name, game_name, game_version, game_server_name, deployment, room)

        app_path = os.path.join(self.binaries_path, GameServersControllerModel.RUNTIME, game_name, game_version, deployment)

        if not os.path.isdir(app_path):
            logging.info(u"Path {0} does not exist (not yet deployed), trying to download...".format(app_path))
            try:
                await self.app.master.download_deployment(game_name, game_version, deployment)
            except Exception:
                raise gameserver.SpawnError("Cannot download deployment {0} (not deployed)".format(app_path))

        sock_name = str(os.getpid()) + "_" + name

        if unix_domain_sockets_enabled():
            sock_path = os.path.join(self.sock_path, sock_name)
        else:
            sock_path = None

        try:
            settings = await instance.spawn(app_path, binary, sock_path, arguments, env, room)
        except gameserver.SpawnError as e:
            logging.error("Failed to spawn server instance: " + e.message)
            await instance.crashed("Failed to spawn server instance: " + e.message)
            raise

        logging.info("New server instance spawned: " + name)

        result = {
            "location": {
                "host": self.app.get_gs_host(),
                "ports": instance.ports
            },
            "settings": settings
        }

        return result

    def __do_remove_server__(self, room_id, server_name):
        self.servers_by_room_id.pop(room_id, None)

        instance = self.servers_by_name.pop(server_name, None)
        if instance:
            instance.dispose()

    async def server_stopped(self, instance):
        self.sub.unsubscribe(instance.pub, ["server_updated"])
        self.pub.notify("server_removed", server=instance)

        room_id = instance.room.id()

        # remove the room from reporting immediately
        self.delete_room(room_id)
        server_name = instance.name

        # the actual game server instance, however, will be removed much later to allow admins to intervene
        # and inspect the status for instances

        IOLoop.current().add_timeout(
            datetime.timedelta(minutes=2),
            self.__do_remove_server__, room_id, server_name)

    async def terminate_all(self, kill=False):
        await multi([s.terminate(kill=kill) for name, s in self.servers_by_name.items()])

    async def started(self, application):
        self.clear_logs_cb.start()

    async def stopped(self):
        self.clear_logs_cb.stop()
        await self.terminate_all(kill=True)


class PoolError(Exception):
    def __init__(self, message):
        self.message = message


class PortsPool(object):
    def __init__(self, port_from, port_to):
        self.ports = list(range(port_from, port_to))

    @staticmethod
    def check_port_busy(port):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            # noinspection PyBroadException
            try:
                sock.bind(("0.0.0.0", port))
            except Exception:
                return True
            else:
                return False

    def acquire(self):

        if not self.ports:
            raise PoolError("No ports in pool left")

        for idx, port in enumerate(self.ports):
            if PortsPool.check_port_busy(port):
                continue
            del self.ports[idx]
            logging.info("PortsPool: Taking a port from pool: {0}".format(port))
            return port

        raise PoolError("No ports in pool left")

    def put(self, port):
        logging.info("PortsPool: Putting a port back into the pool: {0}".format(port))
        self.ports.append(port)
