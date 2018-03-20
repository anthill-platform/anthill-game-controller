
from common.options import options

import common.server
import common.access
import common.sign
import handlers as h

from model.controller import GameServersControllerModel
from model.delivery import DeliveryModel
from model.heartbeat import HeartbeatModel

import admin
import options as _opts


class GameControllerServer(common.server.Server):
    # noinspection PyShadowingNames
    def __init__(self):
        super(GameControllerServer, self).__init__()

        self.gs_host = options.gs_host

        self.gs_controller = GameServersControllerModel(
            self,
            sock_path=options.sock_path,
            binaries_path=options.binaries_path,
            logs_path=options.logs_path,
            logs_max_file_size=options.logs_max_file_size,
            logs_keep_time=options.logs_keep_time,
            ports_pool_from=options.ports_pool_from,
            ports_pool_to=options.ports_pool_to)

        self.delivery = DeliveryModel(self.gs_controller)
        self.heartbeat = HeartbeatModel(self)

    def get_internal_handler(self):
        return h.InternalHandler(self)

    def get_models(self):
        return [self.gs_controller, self.delivery, self.heartbeat]

    def get_handlers(self):
        return [
            (r"/spawn", h.SpawnHandler),
            (r"/terminate", h.TerminateHandler),
            (r"/execute_stdin", h.ExecuteStdInHandler),
            (r"/heartbeat", h.HeartbeatHandler),
            (r"/game/(.+)/(.+)/deployments/(.+)/deliver", h.DeliverDeploymentHandler),
            (r"/game/(.+)/(.+)/deployments/(.+)", h.DeploymentHandler)
        ]

    def get_admin_stream(self):
        return {
            "debug": admin.DebugController
        }

    def get_gs_host(self):
        return self.gs_host


if __name__ == "__main__":
    stt = common.server.init()
    common.access.AccessToken.init([common.access.public()])
    common.server.start(GameControllerServer)
