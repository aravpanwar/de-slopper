class Refusal(Exception):
    """An edit that cannot be applied safely; the message is the receipt reason."""


def purify_text(text: str) -> str:
    # A lone surrogate from a JSON escape would crash the final utf-8 write.
    return text.encode("utf-8", "replace").decode("utf-8")


def one_line(text: str) -> str:
    return " ".join(purify_text(text).split())
