# Positive test cases for CODE-C3.2: Recomputing loop-invariant

# S1: Invariant call in for loop
def test_s1_call_in_for_loop(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
    return total

# S1: Invariant call in while loop
def test_s1_call_in_while_loop(cfg):
    count = 0
    results = []
    while count < 10:
        res = fetch_constant_lookup(cfg)
        results.append(res)
        count += 1
    return results

# S2: Invariant attribute / subscript chain
def test_s2_attribute_subscript_chain(users, settings):
    res = []
    for u in users:
        x = settings.limits["max"] * u.n
        res.append(x)
    return res

# S3: Invariant arithmetic over outer variables
def test_s3_invariant_arithmetic(n, base, margin):
    limits = []
    for i in range(n):
        limit = base + margin
        limits.append(limit)
    return limits

# Nested loop: expression invariant in both loops (should be attributed to outer loop)
def test_nested_loop_outer_attribution(outer_items, inner_items, cfg):
    total = 0
    for outer in outer_items:
        for inner in inner_items:
            total += compute_rate(cfg) * inner
    return total

# Nested loop: expression invariant only in inner loop (attributed to inner loop)
def test_nested_loop_inner_attribution(outer_items, inner_items):
    total = 0
    for outer in outer_items:
        for inner in inner_items:
            total += compute_rate(outer) * inner
    return total
