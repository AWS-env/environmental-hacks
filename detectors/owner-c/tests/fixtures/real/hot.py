import time
def slow_membership(items, probes):
    hits = 0
    for p in probes:
        if p in items:      # list membership in a loop (PY-01 shape)
            hits += 1
    return hits
t=time.time()
items=list(range(20000)); probes=list(range(0,40000,2))
while time.time()-t < 3:
    slow_membership(items, probes)
