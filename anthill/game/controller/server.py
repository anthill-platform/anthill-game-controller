
import os

# supressed default anthill options which do not apply for game-controller
os.environ["NODEFAULTOPS"] = "1"

from anthill.common.options import options
from anthill.common import server, access

from . model.controller import GameServersControllerModel
from . model.delivery import DeliveryModel
from . model.heartbeat import HeartbeatModel
from . model.master import MasterConnectionModel
from . model.debug import DebugControllerModel

from . import options as _opts


class GameControllerServer(server.Server):
    # noinspection PyShadowingNames
    def __init__(self):
        super(GameControllerServer, self).__init__()

        self.gs_host = options.gs_host
        self.debug_controller = DebugControllerModel(self)

        self.master = MasterConnectionModel(
            self,
            username=options.connection_username,
            password=options.connection_password,
            gamespace=options.connection_gamespace,
            region=options.region)

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
        self.heartbeat = HeartbeatModel(self, options.dist_usage_path)

    def get_models(self):
        return [self.debug_controller, self.master, self.gs_controller, self.delivery, self.heartbeat]

    def get_host(self):
        return self.gs_host

    def listen_server(self):
        pass

    def create_token_cache(self):
        return None

    def monitor_action(self, action_name, values, **tags):
        pass

    def monitor_rate(self, action_name, name_property, **tags):
        pass

    async def started(self):
        self.init_discovery()
        await self.models_started()

    def get_gs_host(self):
        return self.gs_host


if __name__ == "__main__":
    stt = server.init()
    access.AccessToken.init([access.public()])
    server.start(GameControllerServer)
