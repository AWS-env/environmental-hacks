import pandas as pd
from math import sin, cos

def compute(x):
    y = sin(x)
    import scipy
    return y
    dead_code = 123
    print("unreachable after return")

def fail_fast():
    raise ValueError("error")
    print("unreachable after raise")

if False:
    print("unreachable constant false branch")

while 0:
    print("unreachable while 0 loop")
