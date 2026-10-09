# Negative test cases for CODE-C3.2: Recomputing loop-invariant
# All examples in this file must generate zero findings.

import re
import time
import uuid
import random
import sqlite3

class MyHandler:
    def __init__(self, config):
        self.config = config

# 1. Operands depend on loop variable
def neg_depends_on_loop_var(rows):
    total = 0
    for r in rows:
        total += compute_rate(r) * r
    return total

# 2. Callee lookups are C10.5 territory, not C3.2
def neg_callee_lookup(xs, out):
    import math
    for x in xs:
        out.append(math.sqrt(x))

# 3. Base operand reassigned in loop body
def neg_reassigned_in_body(rows, cfg, new_cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
        cfg = new_cfg
    return total

# 4. Augmented assignment on base operand in body
def neg_augmented_assign_in_body(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
        cfg += 1
    return total

# 5. Method call on base operand in body (touched/mutated)
def neg_method_call_on_base(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
        cfg.update()
    return total

# 6. Item / attribute store on base operand in body
def neg_store_on_base(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
        cfg.value = 42
        cfg["key"] = 99
    return total

# 7. Base operand passed by name to another call in body
def neg_passed_to_another_call(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
        mutate_object(cfg)
    return total

# 8. Operand declared global or nonlocal in enclosing function
def neg_global_operand(rows):
    global cfg
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
    return total

def neg_nonlocal_operand(rows):
    cfg = {}
    def inner():
        nonlocal cfg
        total = 0
        for r in rows:
            total += compute_rate(cfg) * r
        return total
    return inner()

# 9. Volatile / non-pure calls
def neg_volatile_calls(items, file_obj, iter_obj):
    res = []
    for item in items:
        t = time.time()
        uid = uuid.uuid4()
        rnd = random.random()
        data = file_obj.read()
        n = next(iter_obj)
        res.append((t, uid, rnd, data, n))
    return res

# 10. Heavy setup callees owned by C3.3 (#62)
def neg_c33_setup_callees(rules, rule_pattern, db_path, config):
    for rule in rules:
        pat = re.compile(rule_pattern)
        with open("data.txt") as f:
            pass
        conn = sqlite3.connect(db_path)
        handler = MyHandler(config)

# 11. Trivial O(1) built-ins (len / isinstance / type)
def neg_trivial_builtins(items):
    i = 0
    while i < len(items):
        if isinstance(items[i], int):
            t = type(items[i])
        i += 1

# 12. Already-hoisted form
def neg_already_hoisted(rows, cfg):
    rate = compute_rate(cfg)
    total = 0
    for r in rows:
        total += rate * r
    return total

# 13. Runs at most once (unconditional break / return)
def neg_runs_at_most_once_break(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r
        break
    return total

def neg_runs_at_most_once_return(rows, cfg):
    for r in rows:
        return compute_rate(cfg)

# 14. Comprehensions (out of scope in v1)
def neg_inside_comprehensions(rows, cfg):
    res_list = [compute_rate(cfg) * r for r in rows]
    res_dict = {r: compute_rate(cfg) for r in rows}
    res_set = {compute_rate(cfg) for r in rows}
    return res_list, res_dict, res_set

# 15. Explicit line suppression
def neg_suppressed_line(rows, cfg):
    total = 0
    for r in rows:
        total += compute_rate(cfg) * r  # noqa: CODE-C3.2
    return total

def neg_suppressed_loop_header(rows, cfg):
    total = 0
    for r in rows:  # noqa: CODE-C3.2
        total += compute_rate(cfg) * r
    return total
