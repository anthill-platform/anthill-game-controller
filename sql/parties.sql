CREATE TABLE `parties` (
  `party_id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `gamespace_id` int(10) unsigned NOT NULL,
  `party_num_members` int(11) NOT NULL DEFAULT '2',
  `party_max_members` int(11) NOT NULL DEFAULT '2',
  `game_name` varchar(64) NOT NULL DEFAULT '',
  `game_version` varchar(64) NOT NULL DEFAULT '',
  `game_server_id` int(11) unsigned NOT NULL,
  `region_id` int(10) NOT NULL,
  `party_settings` json NOT NULL,
  `room_settings` json NOT NULL,
  `party_status` enum('CREATED','STARTING','STARTED') NOT NULL DEFAULT 'CREATED',
  `party_close_callback` varchar(64) DEFAULT NULL,
  `party_flags` set('AUTO_START','AUTO_CLOSE') NOT NULL DEFAULT '',
  PRIMARY KEY (`party_id`),
  KEY `game_server_id` (`game_server_id`),
  KEY `region_id` (`region_id`),
  CONSTRAINT `parties_ibfk_1` FOREIGN KEY (`game_server_id`) REFERENCES `game_servers` (`game_server_id`),
  CONSTRAINT `parties_ibfk_2` FOREIGN KEY (`region_id`) REFERENCES `regions` (`region_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;