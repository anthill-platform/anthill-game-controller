
from tornado.gen import coroutine, Return
from tornado.concurrent import run_on_executor
from concurrent.futures import ThreadPoolExecutor

from common.model import Model
from servers import GameServersModel

import os
import hashlib
import zipfile
import stat


class DeliveryError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return str(self.code) + ": " + self.message


class Delivery(object):
    executor = ThreadPoolExecutor(max_workers=4)

    def __init__(self, binaries_path, game_name, game_version, deployment_id, deployment_hash):
        self.binaries_path = binaries_path

        self.deployment_hash_local = hashlib.sha256()
        self.deployment_file = None

        self.game_name = game_name
        self.game_version = game_version
        self.deployment_id = deployment_id
        self.deployment_hash = deployment_hash
        self.deployment_path = None

    @run_on_executor
    def data_received(self, chunk):
        self.deployment_file.write(chunk)
        self.deployment_hash_local.update(chunk)

    @coroutine
    def complete(self):
        calculated_hash = self.deployment_hash_local.hexdigest()

        if calculated_hash != self.deployment_hash:
            raise DeliveryError(400, "Bad hash")

        try:
            self.deployment_file.close()
        except Exception as e:
            raise DeliveryError(500, "Failed to write {0}/{1}/{2}: {3}".format(
                self.game_name, self.game_version, self.deployment_id, str(e)
            ))

        runtime_path = os.path.join(
            self.binaries_path, GameServersModel.RUNTIME)

        if not os.path.isdir(runtime_path):
            try:
                os.mkdir(runtime_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        app_path = os.path.join(self.binaries_path, GameServersModel.RUNTIME, self.game_name)

        if not os.path.isdir(app_path):
            try:
                os.mkdir(app_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        version_path = os.path.join(self.binaries_path, GameServersModel.RUNTIME, self.game_name, self.game_version)

        if not os.path.isdir(version_path):
            try:
                os.mkdir(version_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        app_runtime_path = os.path.join(
            self.binaries_path, GameServersModel.RUNTIME, self.game_name, self.game_version,
            str(self.deployment_id))

        if not os.path.isdir(app_runtime_path):
            try:
                os.mkdir(app_runtime_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        yield self.__unpack__(self.deployment_path, app_runtime_path)

    @run_on_executor
    def __unpack__(self, extract, where):

        with zipfile.ZipFile(extract, "r") as z:

            z.extractall(where)

            for name in z.namelist():
                f_name = os.path.join(where, name)
                st = os.stat(f_name)
                os.chmod(f_name, st.st_mode | stat.S_IEXEC)

    @coroutine
    def init(self):
        deployments_path = os.path.join(self.binaries_path, GameServersModel.DEPLOYMENTS)

        if not os.path.isdir(deployments_path):
            try:
                os.mkdir(deployments_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        app_path = os.path.join(self.binaries_path, GameServersModel.DEPLOYMENTS, self.game_name)

        if not os.path.isdir(app_path):
            try:
                os.mkdir(app_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        version_path = os.path.join(self.binaries_path, GameServersModel.DEPLOYMENTS, self.game_name, self.game_version)

        if not os.path.isdir(version_path):
            try:
                os.mkdir(version_path)
            except Exception as e:
                raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                    self.game_name, self.game_version, self.deployment_id, str(e)
                ))

        self.deployment_path = os.path.join(
            self.binaries_path, GameServersModel.DEPLOYMENTS, self.game_name, self.game_version,
            str(self.deployment_id) + ".zip")

        try:
            self.deployment_file = open(self.deployment_path, "w")
        except Exception as e:
            raise DeliveryError(500, "Failed to deploy {0}/{1}/{2}: {3}".format(
                self.game_name, self.game_version, self.deployment_id, str(e)
            ))


class DeliveryModel(Model):
    def __init__(self, gs):
        super(DeliveryModel, self).__init__()
        self.binaries_path = gs.binaries_path

    @coroutine
    def deliver(self, game_name, game_version, deployment_id, deployment_hash):
        delivery = Delivery(self.binaries_path, game_name, game_version, deployment_id, deployment_hash)
        yield delivery.init()
        raise Return(delivery)