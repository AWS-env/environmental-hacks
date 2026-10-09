import asyncio
import os
import subprocess
import time
import aiohttp
import httpx
import requests


def plain_sync(url):
    time.sleep(1)
    requests.get(url)
    subprocess.run(["ls"])
    os.system("ls")
    input("x")
    open("f")


async def nested_def():
    def helper():
        time.sleep(1)
        requests.get("http://x")
    return helper


async def nested_lambda(loop):
    await asyncio.to_thread(lambda: time.sleep(1))
    await loop.run_in_executor(None, lambda: requests.get("http://x"))


async def passes_function_not_call():
    await asyncio.to_thread(time.sleep, 1)
    await asyncio.to_thread(subprocess.run, ["ls"])


async def awaited_calls(client):
    await asyncio.sleep(1)
    await client.get("http://x")


async def async_clients(url):
    async with httpx.AsyncClient() as client:
        await client.get(url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.text()


async def suppressed():
    time.sleep(1)  # noqa
    time.sleep(1)  # noqa: CODE-C11.4
    requests.get("http://x")  # noqa: ASYNC210
    subprocess.run(["ls"])  # noqa: ASYNC220


async def non_blocking_lookalikes(cfg):
    cfg.sleep(1)
    asyncio.sleep
    other = object()
    other.get("k")


async def session_in_plain_def_only():
    def inner():
        s = requests.Session()
        return s.get("http://x")
    return inner