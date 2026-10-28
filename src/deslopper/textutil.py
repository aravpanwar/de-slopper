class Refusal(Exception):
    """An edit that cannot be applied safely; the message is the receipt reason."""


def clean(text: str) -> str:
    # A lone surrogate from a JSON escape would crash the final utf-8 write.
    return text.encode("utf-8", "replace").decode("utf-8")


def one_line(text: str) -> str:
    return " ".join(clean(text).split())
