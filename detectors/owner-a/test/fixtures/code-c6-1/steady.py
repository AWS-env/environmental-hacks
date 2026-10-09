"""Control workload for a real memray artifact: allocates once and holds the data (no churn)."""


def main():
    table = [bytes(2048) for _ in range(1500)]  # ~3 MB, kept alive to the end
    total = 0
    for row in table:
        total += len(row)
    print(total)


if __name__ == "__main__":
    main()
