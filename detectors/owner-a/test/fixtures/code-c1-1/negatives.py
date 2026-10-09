from __future__ import annotations
import os
import sys  # noqa: F401
from functools import lru_cache
from datetime import datetime
from typing import List, TYPE_CHECKING
import torch
from helper import public_func

__all__ = ["public_func"]

if TYPE_CHECKING:
    import numpy as np

try:
    import optional_package
except ImportError:
    optional_package = None

@lru_cache(maxsize=128)
def process_data(items: List[str], now: datetime = datetime.now()) -> "torch.Tensor":
    return os.path.join(".", items[0])
