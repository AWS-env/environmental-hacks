# Positive cases for CODE-C6.6: each function leaks exactly one handle (synthetic fixtures)
import socket
import sqlite3
from io import open as o
import gzip as gz


def plain_open(path):
    f = open(path)
    return f.read()


def aliased_import(path):
    fh = o(path)
    return fh.readline()


def aliased_module(path):
    g = gz.open(path)
    return g.read()


def raw_socket(host):
    s = socket.socket()
    s.connect((host, 80))
    s.sendall(b"ping")


def db_connection(path):
    conn = sqlite3.connect(path)
    conn.execute("select 1")


def chained_read(path):
    return open(path).read()


def chained_write(path, data):
    open(path, "w").write(data)


def close_not_exception_safe(path):
    f = open(path)
    data = f.read()
    f.close()
    return data
