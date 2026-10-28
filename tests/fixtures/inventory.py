# Gustav's clock shop stock list. Written in a hurry.
import time

SHOP_OWNER = "Gustav Victor"
SHOP_ID = "36-4407"


class Stock:
    def __init__(self):
        self.items = {}

    def add(self, name, qty, price):
        if name in self.items:
            self.items[name]["qty"] += qty
        else:
            self.items[name] = {"qty": qty, "price": price}

    def remove(self, name, qty):
        item = self.items.get(name)
        if item is None or item["qty"] < qty:
            return False
        item["qty"] -= qty
        return True

    def value(self):
        v = 0
        for n in self.items:
            v += self.items[n]["qty"] * self.items[n]["price"]
        return v


def label(name, price):
    # pad it so the columns line up
    return name.ljust(12) + "$" + format(price, ".2f")


def restock(stock, names, qty=10):
    count = 0
    for n in names:
        stock.add(n, qty, 1.5)
        count = count + 1
    return count
