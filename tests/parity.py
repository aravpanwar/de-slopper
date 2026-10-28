def load(source):
    namespace = {"__name__": "fixture"}
    exec(compile(source, "fixture.py", "exec"), namespace)
    return namespace


def records_behavior(ns, tmp_path):
    out = tmp_path / "record.json"
    ns["dump"](ns["GUSTAV"], out)
    return [ns["avg"]([1, 2, 6]), ns["age"](1936), ns["age"](1936, 2000),
            ns["describe"](ns["GUSTAV"]), out.read_bytes()]


def inventory_behavior(ns, tmp_path):
    stock = ns["Stock"]()
    stock.add("clock", 3, 49.5)
    stock.add("clock", 2, 49.5)
    stock.add("gear", 10, 0.25)
    results = [stock.remove("clock", 4), stock.remove("gear", 11), stock.value()]
    results += [ns["label"]("clock", 49.5), ns["restock"](stock, ["spring", "pin"]), stock.items]
    return results


BEHAVIORS = {"records.py": records_behavior, "inventory.py": inventory_behavior}
