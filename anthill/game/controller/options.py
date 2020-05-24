
from anthill.common.options import define
import os
import platform

# Main
define("api_version",
       default="0.2",
       help="Service API version to return to the users in header X-API-Version",
       type=str)

define("auth_key_public",
       default="../.anthill-keys/anthill.pub",
       help="Location of public key required for access token verification.",
       type=str)

define("debug",
       default=False,
       help="Is debug mode enabled (includes full stack trace)",
       type=bool)

define("discovery_service",
       default="http://localhost:9502",
       help="Discovery service location (if applicable).",
       group="discovery",
       type=str)

define("gs_host",
       default=platform.uname()[1],
       help="Public hostname without protocol and port (for application usage), by default resolved to the hostname",
       type=str)

define("name",
       default="game_controller",
       help="Service short name. Used to discover by discovery service.",
       type=str)

define("region",
       default="local",
       help="Name of the region this host belongs to",
       type=str,
       group="gameservers")

define("connection_username",
       default="root",
       help="Username (dev:xxx) with 'game_host' access, which will be used to obtain a connection to the Game Master",
       type=str,
       group="gameservers")

define("connection_password",
       default="anthill",
       help="Password which will be used to obtain a connection to the Game Master",
       type=str,
       group="gameservers")

define("connection_gamespace",
       default="root",
       help="Password which will be used to obtain a connection to the Game Master",
       type=str,
       group="gameservers")

# Game servers

if os.name == "nt":
    # Windows
    define("sock_path",
           default=None,
           help="Location of the unix sockets game servers communicate with.",
           type=str,
           group="gameservers")

    define("binaries_path",
           default="C:/Anthill/game-controller-binaries",
           help="Location of game server binaries.",
           type=str,
           group="gameservers")

    define("logs_path",
           default="C:/Anthill/gameservers",
           help="Location for game server output logs.",
           type=str,
           group="gameservers")
else:
    # Unix
    define("sock_path",
           default="/tmp",
           help="Location of the unix sockets game servers communicate with.",
           type=str,
           group="gameservers")

    define("binaries_path",
           default="/usr/local/anthill/game-controller-binaries",
           help="Location of game server binaries.",
           type=str,
           group="gameservers")

    define("logs_path",
           default="/var/log/gameserver",
           help="Location for game server output logs.",
           type=str,
           group="gameservers")

define("logs_keep_time",
       default=86400,
       help="Time to keep the logs for each game server.",
       type=int,
       group="gameservers")

define("logs_max_file_size",
       default=2000000,
       help="Max file size of a single log file. Once exceeded, log will be written to a new file, and old one "
            "will be eventually cleaned up.",
       type=int,
       group="gameservers")

define("ports_pool_from",
       default=38000,
       help="Port range start (for game servers)",
       type=int,
       group="gameservers")

define("ports_pool_to",
       default=40000,
       help="Port range end (for game servers)",
       type=int,
       group="gameservers")

define("dist_usage_path",
       default="/",
       help="A path used to calculate disk usage and later report to Game Master",
       type=str,
       group="gameservers")
