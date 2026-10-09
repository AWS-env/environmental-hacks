import copy
data = [list(range(2000)) for _ in range(200)]
for _ in range(20):
    c = copy.deepcopy(data)
    t = sum([x for row in c for x in row])
