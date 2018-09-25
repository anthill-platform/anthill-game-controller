
from tornado.ioloop import IOLoop

from concurrent.futures import ThreadPoolExecutor
from tornado.gen import with_timeout, TimeoutError, sleep, Future
from tornado.ioloop import PeriodicCallback
from tornado.process import Subprocess

from anthill.common import events, jsonrpc, run_on_executor

from . import msg

import datetime
import logging
import mmap
import os
import signal
import ujson


class SpawnError(Exception):
    def __init__(self, message):
        self.message = message


class NotifyError(Exception):
    def __init__(self, code, message):
        self.code = code
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
    READ_BUFFER_SIZE = 2048

    executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, gs_controller, game_name, game_version, game_server_name,
                 deployment, name, room, logs_path, logs_max_file_size):
        self.gs_controller = gs_controller

        self.game_name = game_name
        self.game_version = game_version
        self.game_server_name = game_server_name
        self.deployment = deployment

        self.name = name
        self.room = room
        self.ioloop = IOLoop.current()
        self.pipe = None
        self.status = GameServer.STATUS_NONE
        self.msg = None
        self.on_stopped = None
        self.pub = events.Publisher()
        self.init_future = None
        self.read_buffer = bytearray(GameServer.READ_BUFFER_SIZE)
        self.read_mem = memoryview(self.read_buffer)

        # message handlers
        self.handlers = {}

        # and common game config
        game_settings = room.game_settings()

        ports_num = game_settings.get("ports", 1)
        self.ports = []

        # get ports from the pool
        for i in range(0, ports_num):
            self.ports.append(gs_controller.pool.acquire())

        check_period = game_settings.get("check_period", GameServer.CHECK_PERIOD) * 1000

        self.read_cb = PeriodicCallback(self.__recv__, GameServer.READ_PERIOD_MS)
        self.check_cb = PeriodicCallback(self.__check__, check_period)

        self.log_path = logs_path
        self.log_count = 0
        self.log_dirty = False
        self.log = open("{0}.{1}".format(self.log_path, self.log_count), "wb", 1, encoding="utf-8")
        self.log_size = 0
        self.logs_max_file_size = logs_max_file_size

    def is_running(self):
        return self.status == GameServer.STATUS_RUNNING

    def __notify_updated__(self):
        self.pub.notify("server_updated", server=self)

    def set_status(self, status):
        self.status = status

        if self.log is not None:
            self.log.flush()

        self.__notify_updated__()

    def __check__(self):
        if not self.is_running():
            self.check_cb.stop()
            return

        IOLoop.current().add_callback(self.__check_status__)

    async def __check_status__(self):
        try:
            response = await self.msg.send_request(self, "status")
        except jsonrpc.JsonRPCTimeout:
            self.__notify__(u"Timeout to check status")
            await self.terminate(False)
        else:
            if not isinstance(response, dict):
                status = "not_a_dict"
            else:
                status = response.get("status", "bad")
            self.__notify__(u"Status: " + str(status))
            if status != "ok":
                self.__notify__(u"Bad status")
                await self.terminate(False)

    # noinspection PyUnusedLocal
    async def update_settings(self, result, settings, *args, **kwargs):
        self.__notify_updated__()

    async def inited(self, settings):

        self.__handle__("check_deployment", self.__check_deployment__)

        await self.room.update_settings(settings)

        self.__notify__(u"Inited.")
        self.set_status(GameServer.STATUS_RUNNING)
        self.check_cb.start()

        return {
            "status": "OK"
        }

    def __del__(self):
        logging.info(u"[{0}] Server instance has been deleted".format(self.name))

    # noinspection PyMethodMayBeStatic
    async def __prepare_room__(self, room):
        room_settings = room.room_settings()
        server_settings = room.server_settings()
        game_settings = room.game_settings()
        other_settings = room.other_settings()

        max_players = game_settings.get("max_players", 8)

        env = {
            "server_settings": ujson.dumps(server_settings, escape_forward_slashes=False),
            "room_settings": ujson.dumps(room_settings),
            "room_id": str(room.id()),
            "game_max_players": str(max_players)
        }

        if other_settings:
            for key, value in other_settings.items():
                if isinstance(value, dict):
                    env[key] = ujson.dumps(value)
                else:
                    env[key] = str(value)

        token = game_settings.get("token", None)
        if token:
            env["login_access_token"] = token

        discover = game_settings.get("discover", None)
        if discover:
            env["discovery_services"] = ujson.dumps(discover, escape_forward_slashes=False)

        return env

    # noinspection PyUnusedLocal
    async def __cb_stopped__(self, *args, **kwargs):
        if self.init_future is None:
            return

        self.__clear_handle__("stopped")

        self.init_future.set_exception(SpawnError(u"Stopped before 'inited' command received."))
        self.init_future = None

    async def __cb_inited__(self, settings=None):
        if self.init_future is None:
            return

        self.__clear_handle__("inited")
        self.__clear_handle__("stopped")

        # call it, the message will be passed
        self.init_future.set_result(settings or {})
        self.init_future = None

        # we're done initializing
        res_ = await self.inited(settings)
        return res_

    def __wait_for_init_future__(self):
        """
        This "method" returns a Future that will be resolved later, either by:

           1) "inited" method being called (see __cb_inited__) by the gameserver
           2) "stopped" method being called (see __cb_stopped__) either by the crash of the gameserver, or due to
              external interrupt

        :return: a Future
        """
        self.init_future = Future()

        # catch the init message
        self.__handle__("inited", self.__cb_inited__)
        # and the stopped (if one)
        self.__handle__("stopped", self.__cb_stopped__)

        return self.init_future

    async def spawn(self, path, binary, sock_path, cmd_arguments, env, room):

        if not os.path.isdir(path):
            raise SpawnError(u"Game server is not deployed yet")

        if not os.path.isfile(os.path.join(path, binary)):
            raise SpawnError(u"Game server binary is not deployed yet")

        if not isinstance(env, dict):
            raise SpawnError(u"env is not a dict")

        env.update((await self.__prepare_room__(room)))

        await self.listen(sock_path)

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

        for name, value in env.items():
            self.__notify__(u"  " + name + u" = " + value + u";")

        self.set_status(GameServer.STATUS_INITIALIZING)

        try:
            self.pipe = Subprocess(cmd, shell=True, cwd=path, preexec_fn=os.setsid, env=env,
                                   stdin=Subprocess.STREAM, stdout=Subprocess.STREAM, stderr=Subprocess.STREAM)
        except OSError as e:
            reason = u"Failed to spawn a server: " + e.args[1]
            self.__notify__(reason)
            await self.crashed(reason, exitcode=e.errno)

            raise SpawnError(reason)
        else:
            self.pipe.set_exit_callback(self.__exit_callback__)
            self.set_status(GameServer.STATUS_LOADING)
            self.read_cb.start()

        self.__notify__(u"Server '{0}' spawned, waiting for init command.".format(self.name))

        f = self.__wait_for_init_future__()

        # wait, until the 'init' command is received
        # or, the server is stopped (that's bad) earlier
        try:
            settings = await with_timeout(datetime.timedelta(seconds=GameServer.SPAWN_TIMEOUT), f)

            # if the result is an Exception, that means
            # the 'wait' told us so
            if isinstance(settings, Exception):
                raise settings

            return settings
        except TimeoutError:
            self.__notify__(u"Timeout to spawn.")
            await self.terminate(True)
            raise SpawnError(u"Failed to spawn a game server: timeout")

        finally:
            self.init_future = None

    async def send_stdin(self, data):
        self.pipe.stdin.write(data.encode('ascii', 'ignore') + "\n")

    # noinspection PyBroadException
    @run_on_executor
    def __kill__(self):
        try:
            os.killpg(os.getpgid(self.pipe.proc.pid), signal.SIGKILL)
        except Exception as e:
            return str(e)
        else:
            return None

    # noinspection PyBroadException
    @run_on_executor
    def __terminate__(self):
        try:
            os.killpg(os.getpgid(self.pipe.proc.pid), signal.SIGTERM)
        except Exception as e:
            return str(e)
        else:
            return None

    async def terminate(self, kill=False):
        self.__notify__(u"Terminating... (kill={0})".format(kill))

        kill_proc = self.__kill__() if kill else self.__terminate__()

        try:
            error = await with_timeout(datetime.timedelta(seconds=GameServer.TERMINATE_TIMEOUT), kill_proc)
        except TimeoutError:
            self.__notify__(u"Terminate timeout.")

            if kill:
                await self.__stopped__(exitcode=999)
            else:
                await self.terminate(kill=True)
        else:
            if error:
                self.__notify__(u"Failed to terminate: " + str(error))
            else:
                self.__notify__(u"Terminated successfully.")

        if self.log is not None:
            self.log.flush()

    async def stream_log(self, process_line):
        """
        This coroutine streams associated log file by calling process_line(server_name, line) each
        time a new line appears (essentially acts like "tail -f").

        If the process_line call returns False, the streaming stops.

        One the file is closed, stops the iteration
        """

        file_counter = -1
        f = None

        while True:
            if f is None or f.closed:
                file_counter += 1

                try:
                    f = open("{0}.{1}".format(self.log_path, file_counter))
                except OSError:
                    f = None
                    if file_counter > self.log_count:
                        # no next file, so ending
                        return
                    else:
                        continue
            try:
                line = f.readline()
            except OSError:
                continue
            if not line:
                if file_counter == self.log_count:
                    if self.status == GameServer.STATUS_STOPPED:
                        # that's last one and the server is stopped
                        f.close()
                        return
                else:
                    # that's not the last file in the chain, switch to the next one
                    f.close()
                    continue
                await sleep(0.5)
                continue
            if not process_line(self.name, line):
                return

    def __write_log__(self, data):
        self.log.write(data)
        self.log_size += len(data)
        self.log_dirty = True

        if self.log_size > self.logs_max_file_size:
            self.log_count += 1
            self.log_size = 0

            new_log_file = "{0}.{1}".format(self.log_path, self.log_count)

            # open log as a new file, once the limit exceeded
            # eventually the old one will be cleaned up
            self.log.close()
            self.log = open(new_log_file, "wb", 1)
            self.log_dirty = False

            self.__notify__("Swapping log file: {0}".format(new_log_file))

    def __recv__(self):
        """
        This one if often called to poll the STREAM output of the game server
        """

        if self.status == GameServer.STATUS_STOPPED:
            return

        while True:
            err_read_num = self.pipe.stderr.read_from_fd(self.read_buffer)
            if err_read_num is None:
                break
            self.__write_log__(self.read_mem[0:err_read_num])

        while True:
            str_read_num = self.pipe.stdout.read_from_fd(self.read_buffer)
            if str_read_num is None:
                break
            self.__write_log__(self.read_mem[0:str_read_num])

        if self.log_dirty:
            self.log_dirty = False

            try:
                self.log.flush()
            except OSError:
                pass

    # noinspection PyBroadException
    def log_contains_text(self, text):
        for i in range(0, self.log_count + 1):
            try:
                with open("{0}.{1}".format(self.log_path, i)) as f:
                    s = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
                    if s.find(text) != -1:
                        return True
            except Exception:
                continue

    def __exit_callback__(self, exitcode):
        self.check_cb.stop()
        self.read_cb.stop()

        self.ioloop.add_callback(self.__stopped__, exitcode=exitcode)

    async def crashed(self, reason, exitcode=999):
        self.__notify__(reason)
        await self.__stopped__(GameServer.STATUS_ERROR, exitcode=exitcode)

    # noinspection PyUnusedLocal
    async def __stopped__(self, reason=STATUS_STOPPED, exitcode=0):
        if self.status == reason:
            return

        self.set_status(reason)

        self.__notify__(u"Stopped.")

        if self.log is not None:
            self.log.flush()

        await self.gs_controller.server_stopped(self)

        # notify the master server that this server is died
        try:
            await self.command(self, "stopped")
        except jsonrpc.JsonRPCError:
            logging.exception("Failed to notify the server is stopped!")

        await self.release()

    async def release(self):
        if self.log is None:
            return

        # put back the ports acquired at spawn
        if self.ports:
            for port in self.ports:
                self.gs_controller.pool.put(port)

        # noinspection PyBroadException
        try:
            self.pipe.stdout.close_fd()
        except Exception:
            pass

        # noinspection PyBroadException
        try:
            self.pipe.stderr.close_fd()
        except Exception:
            pass

        # noinspection PyBroadException
        try:
            self.pipe.stdin.close_fd()
        except Exception:
            pass

        self.ports = []

        self.log.close()
        self.log = None

        if self.msg:
            await self.msg.release()

        logging.info(u"[{0}] Server has been released".format(self.name))

    def dispose(self):

        self.check_cb = None
        self.read_cb = None
        self.pub = None
        self.gs_controller = None
        self.room = None
        self.handlers = None
        self.msg = None
        self.pipe = None
        self.ports = None
        self.init_future = None

        logging.info(u"[{0}] Server has been disposed".format(self.name))

    def __notify__(self, data):
        if self.log is None:
            return

        self.log.write(data)
        self.log.write("\n".encode("utf-8"))

    def __handle__(self, action, handlers):
        if self.handlers is not None:
            self.handlers[action] = handlers

    def __clear_handle__(self, action):
        if self.handlers is not None:
            self.handlers.pop(action)

    async def __check_deployment__(self):

        """
        Checks if the current deployment of the game server is still up to date
        It wraps the original call because the actual game server does not know
            the deployment_id
        """

        try:
            response = await self.room.notify(
                "check_deployment",
                game_name=self.game_name,
                game_version=self.game_version,
                deployment_id=self.deployment)
        except NotifyError as e:
            raise jsonrpc.JsonRPCError(e.code, e.message)

        return response

    # noinspection PyUnusedLocal
    async def command(self, context, method, *args, **kwargs):
        if (self.handlers is not None) and (method in self.handlers):
            # if this action is registered
            # inside of the internal handlers
            # then catch it
            response = await self.handlers[method](*args, **kwargs)
        else:
            try:
                response = await self.room.notify(method, *args, **kwargs)
            except NotifyError as e:
                raise jsonrpc.JsonRPCError(e.code, e.message)

            # if there's a method with such action name, call it
            if (not method.startswith("_")) and hasattr(self, method):
                await getattr(self, method)(response, *args, **kwargs)

        return response or {}

    async def listen(self, sock_path):
        self.msg = msg.ProcessMessages(path=sock_path)
        self.msg.set_receive(self.command)
        try:
            await self.msg.server()
        except jsonrpc.JsonRPCError as e:
            raise SpawnError(e.message)
