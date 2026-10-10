"""Demo workload for generating a real memray artifact (our own code, run only inside a throwaway container).
It creates many short-lived temporary objects: the pattern CODE-C6.1 (unnecessary object) is about."""


def score(values):
    # builds a new list and a new tuple per call, then throws both away
    doubled = [v * 2 for v in values]
    return sum(tuple(doubled))


def main():
    data = list(range(200))
    total = 0
    for _ in range(3000):
        total += score(data)
    print(total)


if __name__ == "__main__":
    main()
