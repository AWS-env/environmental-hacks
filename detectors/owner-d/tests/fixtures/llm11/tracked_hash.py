"""Synthetic LLM-11 exception: a content hash per file decides what is embedded."""

import glob
import hashlib
import json

from openai import OpenAI

client = OpenAI()
SEEN = json.load(open("hashes.json"))


def refresh(event, context):
    for path in glob.glob("corpus/*.md"):
        text = open(path).read()
        digest = hashlib.sha256(text.encode()).hexdigest()
        print(path, digest, SEEN.get(path))
        client.embeddings.create(model="text-embedding-3-small", input=text)
        SEEN[path] = digest
