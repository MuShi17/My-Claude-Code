def process_data(items):
    results = []
    for item in items:
        if item and len(item.strip()) > 0:
            results.append(item.strip().upper())
    valid = len(results)
    invalid = len(items) - valid
    print(f"Valid: {valid}")
    print(f"Invalid: {invalid}")
    print(f"Total: {len(items)}")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r}")


if __name__ == "__main__":
    process_data(['a', 'b', ''])
