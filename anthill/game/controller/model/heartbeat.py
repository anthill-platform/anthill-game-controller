
from psutil import virtual_memory, cpu_percent, disk_usage
from anthill.common.model import Model


class HeartbeatModel(Model):
    def __init__(self, app, dist_usage_path):
        self.app = app
        self.dist_usage_path = dist_usage_path

    def __rooms_report__(self):
        return [room_id for room_id, room in self.app.gs_controller.list_rooms()]

    def report(self):
        memory_load = int(virtual_memory().percent)
        cpu_load = int(cpu_percent())
        dist_usage = int(disk_usage(self.dist_usage_path).percent)

        rooms = self.__rooms_report__()

        return {
            "load": {
                "memory": memory_load,
                "cpu": cpu_load,
                "storage": dist_usage
            },
            "rooms": rooms
        }
