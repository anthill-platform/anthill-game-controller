# coding=utf-8

from tornado.gen import coroutine, Return, sleep, with_timeout, Task, TimeoutError
from tornado.ioloop import PeriodicCallback
from tornado.concurrent import run_on_executor
from concurrent.futures import ThreadPoolExecutor

import tornado.ioloop

import os
import asyncproc
import logging
import signal
import msg
import datetime

import common.events
import common.jsonrpc
import common.discover

from common.discover import DiscoveryError
from common.internal import Internal, InternalError
from common import retry, SyncTimeout
from room import NotifyError

import ujson
import psutil


class BufferedLog(object):
    COLLECT_TIME = 2
    MAX_LOG_LINES = 512

    def __init__(self, callback):
        self.buffer = []
        self.callback = callback
        self.log = []

    def add(self, data):
        if not self.buffer:
            tornado.ioloop.IOLoop.current().add_timeout(
                datetime.timedelta(seconds=BufferedLog.COLLECT_TIME), self.flush)
        self.buffer.append(unicode(data))

    def get_log(self):
        return u"".join(self.log)

    def flush(self):
        if self.buffer:
            data = u"\n".join(self.buffer) + u"\n"
            self.log.append(data)

            log_length = len(self.log)
            if log_length > BufferedLog.MAX_LOG_LINES + 16:
                to_delete = log_length - BufferedLog.MAX_LOG_LINES
                del self.log[:to_delete]

            self.callback(data)
            self.buffer = []


class SpawnError(Exception):
    def __init__(self, message):
        self.message = message


class GameServer(object):
    STATUS_LOADING = "loading"
    STATUS_INITIALIZING = "initializing"
    STATUS_STOPPED = "stopped"
    STATUS_RUNNING = "running"
    STATUS_ERROR = "error"
    STATUS_NONE = "none"

    SPAWN_TIMEOUT = 30
    TERMINATE_TIMEOUT = 5
    CHECK_PERIOD = 60
    READ_PERIOD_MS = 200

    executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, gs, game_name, game_version, game_server_name, deployment, name, room):
        self.gs = gs

        self.game_name = game_name
        self.game_version = game_version
        self.game_server_name = game_server_name
        self.deployment = deployment

        self.name = name
        self.room = room
        self.ioloop = tornado.ioloop.IOLoop.instance()
        self.pipe = None
        self.status = GameServer.STATUS_NONE
        self.msg = None
        self.on_stopped = None
        self.pub = common.events.Publisher()

        # message handlers
        self.handlers = {}

        # and common game config
        game_settings = room.game_settings()

        ports_num = game_settings.get("ports", 1)
        self.ports = []

        # get ports from the pool
        for i in xrange(0, ports_num):
            self.ports.append(gs.pool.acquire())

        check_period = game_settings.get("check_period", GameServer.CHECK_PERIOD) * 1000

        self.read_cb = PeriodicCallback(self.__recv__, GameServer.READ_PERIOD_MS)
        self.check_cb = PeriodicCallback(self.__check__, check_period)

        self.log = BufferedLog(self.__flush_log__)

    def is_running(self):
        return self.status == GameServer.STATUS_RUNNING

    def __notify_updated__(self):
        self.pub.notify("server_updated", server=self)

    def set_status(self, status):
        self.status = status
        self.log.flush()
        self.__notify_updated__()

    def __check__(self):
        if not self.is_running():
            self.check_cb.stop()
            return

        tornado.ioloop.IOLoop.current().add_callback(self.__check_status__)

    @coroutine
    def __check_status__(self):
        try:
            response = yield self.msg.send_request(self, "status")
        except common.jsonrpc.JsonRPCTimeout:
            self.__notify__(u"Timeout to check status")
            yield self.terminate(False)
        else:
            if not isinstance(response, dict):
                status = "not_a_dict"
            else:
                status = response.get("status", "bad")
            self.__notify__(u"Status: " + unicode(status))
            if status != "ok":
                self.__notify__(u"Bad status")
                yield self.terminate(False)

    @coroutine
    def update_settings(self, result, settings, *args, **kwargs):
        self.__notify_updated__()

    @coroutine
    def inited(self, settings):

        self.__handle__("check_deployment", self.__check_deployment__)

        yield self.room.update_settings(settings)

        self.__notify__(u"Inited.")
        self.set_status(GameServer.STATUS_RUNNING)
        self.check_cb.start()

        raise Return({
            "status": "OK"
        })

    @coroutine
    def __prepare__(self, room):
        room_settings = room.room_settings()
        server_settings = room.server_settings()
        game_settings = room.game_settings()
        other_settings = room.other_settings()

        max_players = game_settings.get("max_players", 8)

        env = {
            "server:settings": ujson.dumps(server_settings, escape_forward_slashes=False),
            "room:settings": ujson.dumps(room_settings),
            "room:id": str(room.id()),
            "game:max_players": str(max_players)
        }

        if other_settings:
            for key, value in other_settings.iteritems():
                if isinstance(value, dict):
                    env[key] = ujson.dumps(value)
                else:
                    env[key] = str(value)

        token = game_settings.get("token", None)
        if token:
            env["login:access_token"] = token

        discover = game_settings.get("discover", None)
        if discover:
            env["discovery:services"] = ujson.dumps(discover, escape_forward_slashes=False)

        raise Return(env)

    @coroutine
    def spawn(self, path, binary, sock_path, cmd_arguments, env, room):

        if not os.path.isdir(path):
            raise SpawnError(u"Game server is not deployed yet")

        if not os.path.isfile(os.path.join(path, binary)):
            raise SpawnError(u"Game server binary is not deployed yet")

        if not isinstance(env, dict):
            raise SpawnError(u"env is not a dict")

        env.update((yield self.__prepare__(room)))

        yield self.listen(sock_path)

        arguments = [
            # application binary
            os.path.join(path, binary),
            # first the socket
            sock_path,
            # then the ports
            ",".join(str(port) for port in self.ports)
        ]
        # and then custom arguments
        arguments.extend(cmd_arguments)

        cmd = " ".join(arguments)
        self.__notify__(u"Spawning: " + cmd)

        self.__notify__(u"Environment:")

        for name, value in env.iteritems():
            self.__notify__(u"  " + name + u" = " + value + u";")

        self.set_status(GameServer.STATUS_INITIALIZING)

        try:
            self.pipe = asyncproc.Process(cmd, shell=True, cwd=path, preexec_fn=os.setsid, env=env)
        except OSError as e:
            reason = u"Failed to spawn a server: " + e.args[1]
            self.__notify__(reason)
            yield self.crashed(reason)

            raise SpawnError(reason)
        else:
            self.set_status(GameServer.STATUS_LOADING)
            self.read_cb.start()

        self.__notify__(u"Server '{0}' spawned, waiting for init command.".format(self.name))

        def wait(callback):
            @coroutine
            def stopped(*args, **kwargs):
                self.__clear_handle__("stopped")
                callback(SpawnError(u"Stopped before 'inited' command received."))

            @coroutine
            def inited(settings=None):
                self.__clear_handle__("inited")
                self.__clear_handle__("stopped")

                # call it, the message will be passed
                callback(settings or {})

                # we're done initializing
                res_ = yield self.inited(settings)
                raise Return(res_)

            # catch the init message
            self.__handle__("inited", inited)
            # and the stopped (if one)
            self.__handle__("stopped", stopped)

        # wait, until the 'init' command is received
        # or, the server is stopped (that's bad) earlier
        try:
            settings = yield with_timeout(
                datetime.timedelta(seconds=GameServer.SPAWN_TIMEOUT),
                Task(wait))

            # if the result is an Exception, that means
            # the 'wait' told us so
            if isinstance(settings, Exception):
                raise settings

            raise Return(settings)
        except TimeoutError:
            self.__notify__(u"Timeout to spawn.")
            yield self.terminate(True)
            raise SpawnError(u"Failed to spawn a game server: timeout")

    @coroutine
    def send_stdin(self, data):
        self.pipe.write(data.encode('ascii', 'ignore') + "\n")

    # noinspection PyBroadException
    @run_on_executor
    def __kill__(self, code):
        try:
            self.pipe.kill(code)
        except Exception as e:
            return str(e)
        else:
            return None

    @coroutine
    def terminate(self, kill=False):
        self.__notify__(u"Terminating... (kill={0})".format(kill))

        kill_proc = self.__kill__(signal.SIGKILL if kill else signal.SIGTERM)

        try:
            error = yield with_timeout(datetime.timedelta(seconds=GameServer.TERMINATE_TIMEOUT), kill_proc)
        except TimeoutError:
            self.__notify__(u"Terminate timeout.")

            if kill:
                yield self.__stopped__()
            else:
                yield self.terminate(kill=True)
        else:
            if error:
                self.__notify__(u"Failed to terminate: " + str(error))
            else:
                self.__notify__(u"Terminated successfully.")

        self.log.flush()

    def get_log(self):
        return self.log.get_log()

    def has_log(self, text):
        return text in self.log.get_log()

    def __recv__(self):
        if self.status == GameServer.STATUS_STOPPED:
            return

        err_data = self.pipe.readerr()
        if err_data:
            self.__notify__(err_data)

        str_data = self.pipe.read()
        if str_data:
            self.__notify__(str_data)

        # noinspection PyBroadException
        try:
            poll = self.pipe.wait(os.WNOHANG)
        except:
            # totally may happen
            self.__recv_stop__()
        else:
            if poll is not None:
                self.__recv_stop__()

    def __recv_stop__(self):
        self.read_cb.stop()
        self.check_cb.stop()

        self.ioloop.add_callback(self.__stopped__)

    @coroutine
    def crashed(self, reason):
        self.__notify__(reason)
        yield self.__stopped__(GameServer.STATUS_ERROR)

    @coroutine
    def __stopped__(self, reason=STATUS_STOPPED):
        if self.status == reason:
            return

        self.set_status(reason)

        yield self.release()

        self.__notify__(u"Stopped.")
        self.log.flush()

        # notify the master server that this server is died
        try:
            yield self.command(self, "stopped")
        except common.jsonrpc.JsonRPCError:
            logging.exception("Failed to notify the server is stopped!")

        yield self.gs.server_stopped(self)

        self.log.flush()

    @coroutine
    def release(self):
        if self.msg:
            yield self.msg.release()

        if self.room:
            yield self.room.release()

        # put back the ports acquired at spawn
        if self.ports:
            for port in self.ports:
                self.gs.pool.put(port)

        self.ports = []

    def __flush_log__(self, data):
        self.pub.notify("log", name=self.name, data=data)
        logging.info(u"[{0}] {1}".format(self.name, data))

    def __notify__(self, data):
        self.log.add(data)

    def __handle__(self, action, handlers):
        self.handlers[action] = handlers

    def __clear_handle__(self, action):
        self.handlers.pop(action)

    @coroutine
    def __check_deployment__(self):

        """
        Checks if the current deployment of the game server is still up to date
        It wraps the original call because the actual game server does not know
            the deployment_id
        """

        try:
            response = yield self.room.notify(
                "check_deployment",
                game_name=self.game_name,
                game_version=self.game_version,
                deployment_id=self.deployment)
        except NotifyError as e:
            raise common.jsonrpc.JsonRPCError(e.code, e.message)

        raise Return(response)

    @coroutine
    def command(self, context, method, *args, **kwargs):
        if method in self.handlers:
            # if this action is registered
            # inside of the internal handlers
            # then catch it
            response = yield self.handlers[method](*args, **kwargs)
        else:
            try:
                response = yield self.room.notify(method, *args, **kwargs)
            except NotifyError as e:
                raise common.jsonrpc.JsonRPCError(e.code, e.message)

            # if there's a method with such action name, call it
            if (not method.startswith("_")) and hasattr(self, method):
                yield getattr(self, method)(response, *args, **kwargs)

        raise Return(response or {})

    @coroutine
    def listen(self, sock_path):
        self.msg = msg.ProcessMessages(path=sock_path)
        self.msg.set_receive(self.command)
        try:
            yield self.msg.server()
        except common.jsonrpc.JsonRPCError as e:
            raise SpawnError(e.message)
