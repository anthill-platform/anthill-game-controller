import os


def unix_domain_sockets_enabled():
    return os.name != "nt"
