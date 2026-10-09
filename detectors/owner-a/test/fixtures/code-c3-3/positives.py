# Positive cases for CODE-C3.3: inefficient per-iteration setup (synthetic fixtures)
import json
import re

import boto3
import requests
from jinja2 import Template as JTemplate
from requests import Session


def s1_compile(lines, rule):
    for line in lines:
        rx = re.compile(rule)
        if rx.search(line):
            handle(line)


def s1_template(rows, src):
    for row in rows:
        tpl = JTemplate(src)
        emit(tpl.render(row=row))


def s2_session_assign(urls):
    for url in urls:
        s = requests.Session()
        s.get(url)


def s2_session_with(urls):
    for url in urls:
        with requests.Session() as s:
            s.get(url)


def s2_aliased_import(urls):
    for url in urls:
        s = Session()
        s.get(url)


def s2_boto3_client(keys, bucket):
    for key in keys:
        client = boto3.client("s3", region_name="eu-north-1")
        client.get_object(Bucket=bucket, Key=key)


def s3_open_read(keys, path):
    for key in keys:
        with open(path) as f:
            lookup(f, key)


def s3_nested_open(keys, path):
    for key in keys:
        data = json.load(open(path, "rb"))
        use(data[key])


def s4_capwords(rows, cfg):
    for row in rows:
        fmt = Formatter(cfg)
        emit(fmt.format(row))


def nested_outer_attribution(xs, ys, rule):
    for x in xs:
        for y in ys:
            rx = re.compile(rule)
            rx.match(x + y)
