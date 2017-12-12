
from common.options import define

# Main

define("host",
       default="http://localhost:9509",
       help="Public hostname of this service",
       type=str)

define("gs_host",
       default="localhost",
       help="Public hostname without protocol and port (for application usage)",
       type=str)

define("listen",
       default="port:9509",
       help="Public hostname of this service for games (without protocol)",
       type=str)

define("name",
       default="game_controller",
       help="Service short name. Used to discover by discovery service.",
       type=str)

# Game servers

define("sock_path",
       default="/tmp",
       help="Location of the unix sockets game servers communicate with.",
       type=str)

define("binaries_path",
       default="/usr/local/anthill/game-controller-binaries",
       help="Location of game server binaries.",
       type=str)

define("ports_pool_from",
       default=38000,
       help="Port range start (for game servers)",
       type=int)

define("ports_pool_to",
       default=40000,
       help="Port range end (for game servers)",
       type=int)
