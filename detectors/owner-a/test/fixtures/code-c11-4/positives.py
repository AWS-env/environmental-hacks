import os
import subprocess
import time
import time as t
import urllib.request
from time import sleep
import requests
import httpx


async def sleeper():
    time.sleep(1)


async def aliased_module():
    t.sleep(2)


async def aliased_from():
    sleep(3)


async def http_requests(url):
    return requests.get(url)


async def http_post(url, body):
    return requests.post(url, json=body)


async def fetch_urllib(url):
    return urllib.request.urlopen(url)


async def fetch_httpx(url):
    return httpx.get(url)


async def run_proc():
    subprocess.run(["ls"])


async def shell():
    os.system("ls")


async def pipe():
    return os.popen("ls")


async def ask():
    return input("name? ")


async def read_file(path):
    with open(path) as fh:
        return fh.read()


async def session_var(url):
    s = requests.Session()
    return s.get(url)


class Service:
    async def handler(self):
        time.sleep(0.5)
        time.sleep(0.5)