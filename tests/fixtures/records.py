# patient records, quick and dirty
# TODO tidy this up, found it in the café drawer
import json

GUSTAV = {"name": "Gustav Victor", "born": "1936-10-27", "email": "gustav@victor.test", "mrn": "36-4407"}


def avg(xs):
    t = 0
    for x in xs:
        t = t + x
    return t / len(xs)   # blows up on empty, whatever


def age(born_year, now=2026):
    a = now - born_year
    return a


def describe(p):
    s = p["name"] + " (" + p["mrn"] + ")"
    return s


def dump(p, path):
    f = open(path, "w")
    json.dump(p, f)
    f.close()
