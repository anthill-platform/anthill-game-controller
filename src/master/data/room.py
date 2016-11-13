
import ujson
import logging

from common.model import Model
from tornado.gen import coroutine, Return, sleep
from tornado.ioloop import IOLoop

import common.database
from common.internal import Internal, InternalError
from common import random_string


class ApproveFailed(Exception):
    pass


class RoomError(Exception):
    def __init__(self, message):
        self.message = message

    def __str__(self):
        return self.message


class RoomNotFound(Exception):
    pass


class RoomAdapter(object):
    def __init__(self, data):
        self.room_id = str(data.get("room_id"))
        self.host_id = str(data.get("host_id"))
        self.room_settings = data.get("settings", {})
        self.players = data.get("players", 0)
        self.location = data.get("location", {})
        self.game_name = data.get("game_name")
        self.game_version = data.get("game_version")
        self.max_players = data.get("max_players", 8)

    def dump(self):
        return {
            "id": self.room_id,
            "settings": self.room_settings,
            "players": self.players,
            "location": self.location,
            "game_name": self.game_name,
            "game_version": self.game_version,
            "max_players": self.max_players
        }


class RoomQuery(object):
    def __init__(self, gamespace_id, game_name, game_version=None, game_server_id=None):
        self.gamespace_id = gamespace_id
        self.game_name = game_name
        self.game_version = game_version
        self.game_server_id = game_server_id

        self.room_id = None
        self.host_id = None
        self.state = None
        self.ignore_full = False
        self.hosts_order = None
        self.limit = 0
        self.other_conditions = []
        self.for_update = False

    def add_conditions(self, conditions):

        if not isinstance(conditions, list):
            raise RuntimeError("conditions expected to be a list")

        self.other_conditions.extend(conditions)

    def __values__(self):
        conditions = [
            "`gamespace_id`=%s",
            "`game_name`=%s"
        ]

        data = [
            str(self.gamespace_id),
            self.game_name
        ]

        if self.game_version:
            conditions.append("`game_version`=%s")
            data.append(self.game_version)

        if self.game_server_id:
            conditions.append("`game_server_id`=%s")
            data.append(str(self.game_server_id))

        if self.state:
            conditions.append("`state`=%s")
            data.append(self.state)

        if self.ignore_full:
            conditions.append("`players`<`max_players`")

        if self.host_id:
            conditions.append("`host_id`=%s")
            data.append(str(self.host_id))

        if self.room_id:
            conditions.append("`room_id`=%s")
            data.append(str(self.room_id))

        for condition, value in self.other_conditions:
            conditions.append(condition)
            data.append(value)

        return conditions, data

    def query(self):

        conditions, data = self.__values__()

        query = """
            SELECT * FROM `rooms`
            WHERE {0}
        """.format(" AND ".join(conditions))

        if self.hosts_order and not self.host_id:
            query += "ORDER BY FIELD(host_id, {0})".format(
                ", ".join(["%s"] * len(self.hosts_order))
            )
            data.extend(self.hosts_order)

        if self.limit:
            query += """
                LIMIT %s
            """
            data.append(str(self.limit))

        if self.for_update:
            query += """
                FOR UPDATE
            """

        query += ";"

        return query, data


class RoomsModel(Model):

    AUTO_REMOVE_TIME = 10

    @staticmethod
    def __generate_key__(gamespace_id, account_id):
        return str(gamespace_id) + "_" + str(account_id) + "_" + random_string(32)

    @coroutine
    def __inc_players_num__(self, room_id, db):
        yield db.execute(
            """
            UPDATE `rooms` r
            SET `players`=`players`+1
            WHERE `room_id`=%s
            """, room_id
        )

    def get_setup_db(self):
        return self.db

    def get_setup_tables(self):
        return ["rooms", "players"]

    def get_setup_triggers(self):
        return ["player_removal"]

    def __init__(self, db):
        self.db = db
        self.internal = Internal()

    @coroutine
    def get_players_count(self):
        try:
            count = yield self.db.get(
                """
                SELECT COUNT(*) AS `count` FROM `players`
                WHERE `state`='JOINED'
                """
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get players count: " + e.args[1])

        raise Return(count["count"])

    @coroutine
    def __insert_player__(self, gamespace, account_id, room_id, key, access_token, db, trigger_remove=True):
        record_id = yield db.insert(
            """
            INSERT INTO `players`
            (`gamespace_id`, `account_id`, `room_id`, `key`, `access_token`)
            VALUES (%s, %s, %s, %s, %s);
            """, gamespace, account_id, room_id, key, access_token
        )

        if trigger_remove:
            self.trigger_remove_temp_reservation(gamespace, room_id, account_id)

        raise Return(record_id)

    def trigger_remove_temp_reservation(self, gamespace, room_id, account_id):
        IOLoop.current().spawn_callback(self.__remove_temp_reservation__, gamespace, room_id, account_id)

    @coroutine
    def __update_players_num__(self, room_id, db):
        yield db.execute(
            """
            UPDATE `rooms` r
            SET `players`=(SELECT COUNT(*) FROM `players` p WHERE p.room_id = r.room_id)
            WHERE `room_id`=%s
            """, room_id
        )

    @coroutine
    def __remove_temp_reservation__(self, gamespace, room_id, account_id):
        """
        Called asynchronously when user joined the room
        Waits a while, and then leaves the room, if the join reservation
            was not approved by game-controller.
        """

        # wait a while
        yield sleep(RoomsModel.AUTO_REMOVE_TIME)

        result = yield self.leave_room_reservation(gamespace, room_id, account_id)

        if result:
            logging.warning("Removed player reservation: room '{0}' player '{1}' gamespace '{2}'".format(
                room_id, account_id, gamespace
            ))

    @coroutine
    def approve_join(self, gamespace, room_id, key):

        with (yield self.db.acquire(auto_commit=False)) as db:
            try:
                select = yield db.get(
                    """
                    SELECT `access_token`, `record_id`
                    FROM `players`
                    WHERE `gamespace_id`=%s AND `room_id`=%s AND `key`=%s
                    FOR UPDATE;
                    """, gamespace, room_id, key
                )
            except common.database.DatabaseError as e:
                raise RoomError("Failed to approve a join: " + e.args[1])

            if select is None:
                raise ApproveFailed()

            record_id = select["record_id"]
            access_token = select["access_token"]

            try:
                yield db.execute(
                    """
                    UPDATE `players`
                    SET `state`='JOINED'
                    WHERE `gamespace_id`=%s AND `record_id`=%s;
                    """, gamespace, record_id
                )
                yield db.commit()

            except common.database.DatabaseError as e:
                raise RoomError("Failed to approve a join: " + e.args[1])

            raise Return(access_token)

    @coroutine
    def approve_leave(self, gamespace, room_id, key):
        try:
            with (yield self.db.acquire()) as db:
                yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `key`=%s AND `room_id`=%s;
                    """, gamespace, key, room_id
                )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    @coroutine
    def assign_location(self, gamespace, room_id, location):

        if not isinstance(location, dict):
            raise RoomError("Location should be a dict")

        try:
            yield self.db.execute(
                """
                UPDATE `rooms`
                SET `location`=%s, `state`='SPAWNED'
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, ujson.dumps(location), gamespace, room_id
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to create room: " + e.args[1])
        else:
            raise Return(room_id)

    @coroutine
    def create_and_join_room(self, gamespace, game_name, game_version, gs, room_settings,
                             account_id, access_token, host_id, trigger_remove=True):

        max_players = gs.max_players

        key = RoomsModel.__generate_key__(gamespace, account_id)

        try:
            room_id = yield self.db.insert(
                """
                INSERT INTO `rooms`
                (`gamespace_id`, `game_name`, `game_version`, `game_server_id`, `players`,
                  `max_players`, `location`, `settings`, `state`, `host_id`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'NONE', %s)
                """, gamespace, game_name, game_version, gs.game_server_id, 1, max_players,
                "{}", ujson.dumps(room_settings), host_id
            )

            record_id = yield self.__insert_player__(gamespace, account_id, room_id, key, access_token, self.db,
                                                     trigger_remove)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to create a room: " + e.args[1])
        else:
            raise Return((record_id, key, room_id))

    @coroutine
    def find_and_join_room(self, gamespace, game_name, game_version, game_server_id,
                           account_id, access_token, settings,
                           hosts_order=None, my_region_only=None):

        """
        Find the room and join into it, if any
        :param gamespace: the gamespace
        :param game_name: the game ID (string)
        :param game_version: the game's version (string, like 1.0)
        :param game_server_id: game server configuration id
        :param account_id: account of the player
        :param access_token: active player's access token
        :param settings: room specific filters, defined like so:
                {"filterA": 5, "filterB": true, "filterC": {"@func": ">", "@value": 10}}
        :param hosts_order: a list of host id's to order result around
        :param my_region_only: an id of the region the search should be locked around
        :returns a pair of record_id, a key (an unique string to find the record by) for the player and room info
        """
        try:
            conditions = common.database.format_conditions_json('settings', settings)
        except common.database.ConditionError as e:
            raise RoomError(e.message)

        try:
            with (yield self.db.acquire(auto_commit=False)) as db:

                query = RoomQuery(gamespace, game_name, game_version, game_server_id)

                query.add_conditions(conditions)
                query.hosts_order = hosts_order
                query.for_update = True

                text, data = query.query()

                # search for a room first (and lock it for a while)
                room = yield db.get(text, *data)

                if room is None:
                    yield db.commit()
                    raise RoomNotFound()

                room_id = room["room_id"]

                # at last, join into the player list
                record_id, key = yield self.__join_room__(gamespace, room_id, account_id, access_token, db)
                raise Return((record_id, key, RoomAdapter(room)))

        except common.database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

    @coroutine
    def join_room(self, gamespace, game_name, room_id, account_id, access_token):

        """
        Find the room and join into it, if any
        :param gamespace: the gamespace
        :param game_name: the game ID (string)
        :param room_id: an ID of the room join to
        :param account_id: account of the player
        :param access_token: active player's access token
        :returns a pair of record_id, a key (an unique string to find the record by) for the player and room info
        """

        try:
            with (yield self.db.acquire(auto_commit=False)) as db:

                query = RoomQuery(gamespace, game_name)

                query.room_id = room_id
                query.for_update = True

                text, data = query.query()

                # search for a room first (and lock it for a while)
                room = yield db.get(text, *data)

                if room is None:
                    yield db.commit()
                    raise RoomNotFound()

                room_id = room["room_id"]

                # at last, join into the player list
                record_id, key = yield self.__join_room__(gamespace, room_id, account_id, access_token, db)
                raise Return((record_id, key, RoomAdapter(room)))

        except common.database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

    @coroutine
    def find_room(self, gamespace, game_name, game_version, game_server_id, settings, hosts_order=None):

        try:
            conditions = common.database.format_conditions_json('settings', settings)
        except common.database.ConditionError as e:
            raise RoomError(e.message)

        try:

            query = RoomQuery(gamespace, game_name, game_version, game_server_id)

            query.add_conditions(conditions)
            query.hosts_order = hosts_order
            query.state = 'SPAWNED'
            query.limit = 1

            text, data = query.query()

            room = yield self.db.get(text, *data)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        if room is None:
            raise RoomNotFound()

        raise Return(RoomAdapter(room))

    @coroutine
    def update_room_settings(self, gamespace, room_id, room_settings):

        if not isinstance(room_settings, dict):
            raise RoomError("Room settings is not a dict")

        try:
            yield self.db.execute(
                """
                UPDATE `rooms`
                SET `settings`=%s
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, ujson.dumps(room_settings), gamespace, room_id
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to update a room: " + e.args[1])

    @coroutine
    def get_room(self, gamespace, room_id):
        try:
            room = yield self.db.get(
                """
                SELECT * FROM `rooms`
                WHERE `gamespace_id`=%s AND `room_id`=%s
                """, gamespace, room_id
            )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        if room is None:
            raise RoomNotFound()

        raise Return(RoomAdapter(room))

    @coroutine
    def instantiate(self, gamespace, game_id, game_version, game_server_name,
                    room_id, server_host, settings):

        try:
            result = yield self.internal.post(
                server_host, "spawn",
                {
                    "game_id": game_id,
                    "game_version": game_version,
                    "game_server_name": game_server_name,
                    "room_id": room_id,
                    "gamespace": gamespace,
                    "settings": ujson.dumps(settings)
                }, discover_service=False)

        except InternalError as e:
            raise RoomError("Failed to spawn a new game server: " + str(e.code) + " " + e.body)

        raise Return(result)

    @coroutine
    def __join_room__(self, gamespace, room_id, account_id, access_token, db):
        """
        Joins the player to the room
        :param gamespace: the gamespace
        :param room_id: a room to join to
        :param account_id: account of the player
        :param access_token: active player's access token
        :param db: a reference to database instance (optional)

        :returns a pair of record id and a key (an unique string to find the record by)
        """

        key = RoomsModel.__generate_key__(gamespace, account_id)

        try:
            # increment player count (virtually)
            yield self.__inc_players_num__(room_id, db)
            yield db.commit()

            record_id = yield self.__insert_player__(gamespace, account_id, room_id, key, access_token, db, True)
            yield db.commit()

        except common.database.DatabaseError as e:
            raise RoomError("Failed to join a room: " + e.args[1])

        raise Return((record_id, key))

    @coroutine
    def leave_room(self, gamespace, room_id, account_id, remove_room=False):
        try:
            with (yield self.db.acquire()) as db:
                yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id`=%s AND `room_id`=%s;
                    """, gamespace, account_id, room_id
                )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])
        finally:
            if remove_room:
                yield self.remove_room(gamespace, room_id)

    @coroutine
    def leave_room_reservation(self, gamespace, room_id, account_id):
        try:
            with (yield self.db.acquire()) as db:
                result = yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `account_id`=%s AND `room_id`=%s AND `state`='RESERVED';
                    """, gamespace, account_id, room_id
                )

                raise Return(result)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    @coroutine
    def list_rooms(self, gamespace, game_name, game_version, game_server_id, settings,
                   hosts_order=None, ignore_full=True, my_host_only=None):

        try:
            conditions = common.database.format_conditions_json('settings', settings)
        except common.database.ConditionError as e:
            raise RoomError(e.message)

        try:
            query = RoomQuery(gamespace, game_name, game_version, game_server_id)

            query.add_conditions(conditions)
            query.hosts_order = hosts_order
            query.ignore_full = ignore_full
            query.state = 'SPAWNED'
            query.host_id = my_host_only

            text, data = query.query()

            rooms = yield self.db.query(text, *data)
        except common.database.DatabaseError as e:
            raise RoomError("Failed to get room: " + e.args[1])

        raise Return(map(RoomAdapter, rooms))

    @coroutine
    def remove_room(self, gamespace, room_id):
        try:
            # cleanup empty room

            with (yield self.db.acquire()) as db:
                yield db.execute(
                    """
                    DELETE FROM `players`
                    WHERE `gamespace_id`=%s AND `room_id`=%s;
                    """, gamespace, room_id
                )
                yield db.execute(
                    """
                    DELETE FROM `rooms`
                    WHERE `room_id`=%s AND `gamespace_id`=%s;
                    """, room_id, gamespace
                )
        except common.database.DatabaseError as e:
            raise RoomError("Failed to leave a room: " + e.args[1])

    @coroutine
    def spawn_server(self, gamespace, game_id, game_version, game_server_name,
                     room_id, host, settings):

        result = yield self.instantiate(gamespace, game_id, game_version, game_server_name,
                                        room_id, host.internal_location, settings)

        if "location" not in result:
            raise RoomError("No location in result.")

        location = result["location"]

        yield self.assign_location(gamespace, room_id, location)

        raise Return(result)
