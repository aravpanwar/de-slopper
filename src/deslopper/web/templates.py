import html

from deslopper.textutil import Refusal, one_line

TAILWIND_URL = "https://cdn.tailwindcss.com/3.4.17"
FONT_URL = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"

TAILWIND_CONFIG = (
    "tailwind.config = {darkMode: 'class', theme: {extend: {fontFamily: "
    "{sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif']}}}};"
)

BODY_CLASSES = "font-sans antialiased bg-white text-slate-900 dark:bg-slate-950 dark:text-slate-100"

FADE_CSS = (
    ".fade-in{opacity:0;transform:translateY(1.5rem);transition:opacity .7s ease,transform .7s ease}"
    ".fade-in.is-visible{opacity:1;transform:none}"
)

DARK_BUTTON = (
    '<button type="button" id="theme-toggle" aria-label="Toggle dark mode" '
    'class="fixed right-4 top-4 z-50 rounded-full bg-white/80 p-3 text-xl shadow-lg backdrop-blur '
    'transition hover:scale-110 dark:bg-slate-800/80">🌙</button>'
)

DARK_SCRIPT = """(function () {
  var root = document.documentElement;
  var button = document.getElementById("theme-toggle");
  var stored = null;
  try { stored = window.localStorage.getItem("theme"); } catch (error) { stored = null; }
  var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  function render() {
    root.classList.toggle("dark", dark);
    if (button) { button.textContent = dark ? "☀️" : "\U0001F319"; }
  }
  if (button) {
    button.addEventListener("click", function () {
      dark = !dark;
      try { window.localStorage.setItem("theme", dark ? "dark" : "light"); } catch (error) { }
      render();
    });
  }
  render();
})();"""

FADE_SCRIPT = """(function () {
  var targets = document.querySelectorAll("[data-fade]");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!("IntersectionObserver" in window) || reduced) { return; }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("is-visible");
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.1 });
  targets.forEach(function (element) {
    element.classList.add("fade-in");
    observer.observe(element);
  });
})();"""

LAYOUTS = {
    "section": ("section", "mx-auto max-w-5xl px-6 py-12"),
    "card": ("div", "rounded-2xl bg-white p-8 shadow-xl ring-1 ring-slate-100 "
                    "dark:bg-slate-800 dark:ring-slate-700"),
    "grid": ("div", "grid gap-6 md:grid-cols-2"),
    "container": ("div", "mx-auto max-w-5xl px-6"),
}

BLOCKS = ("hero", "stats", "features", "testimonials", "faq", "cta", "footer")
TOP_BLOCKS = ("hero", "stats", "features")
BOTTOM_BLOCKS = ("testimonials", "faq", "cta", "footer")


def _e(value: str) -> str:
    return html.escape(value, quote=True)


def _text(source: dict, key: str, limit: int, default: str | None = None) -> str:
    value = source.get(key)
    if value is None and default is not None:
        return default
    cleaned = one_line(value) if isinstance(value, str) else ""
    if not cleaned:
        raise Refusal(f"expected a string {key}")
    if len(cleaned) > limit:
        raise Refusal(f"expected {key} of at most {limit} characters")
    return cleaned


def _optional(source: dict, key: str, limit: int) -> str | None:
    return _text(source, key, limit) if source.get(key) not in (None, "") else None


def _items(fields: dict, block: str, shape: dict[str, int], low: int, high: int) -> list[dict]:
    raw = fields.get(block, fields.get("items"))
    if not isinstance(raw, list):
        raise Refusal(f"expected a list of {block}")
    if len(raw) < low:
        raise Refusal(f"expected at least {low} {block}, got {len(raw)}")
    found = []
    for item in raw[:high]:
        if not isinstance(item, dict):
            raise Refusal(f"expected each of the {block} to be an object")
        found.append({key: _text(item, key, limit) for key, limit in shape.items()})
    return found


def _heading(title: str, subtitle: str | None) -> str:
    text = f'<h2 class="text-3xl font-bold tracking-tight sm:text-4xl">{_e(title)}</h2>'
    if subtitle:
        text += (f'<p class="mx-auto mt-4 max-w-2xl text-lg text-slate-600 '
                 f'dark:text-slate-400">{_e(subtitle)}</p>')
    return f'<div class="text-center">{text}</div>'


def render(block: str, fields: dict) -> str:
    """Markup for one block. Every piece of model text is escaped; nothing else is variable."""
    if block not in BLOCKS:
        raise Refusal(f"expected a known block, got {block}")
    return globals()[f"_{block}"](fields)


def _hero(fields: dict) -> str:
    headline = _text(fields, "headline", 120)
    subheadline = _text(fields, "subheadline", 300)
    cta = _text(fields, "cta", 40, "Get Started")
    badge = _optional(fields, "badge", 60)
    badge_html = (
        f'<span class="inline-block rounded-full bg-white/20 px-4 py-1 text-sm font-medium '
        f'backdrop-blur">{_e(badge)}</span>' if badge else ""
    )
    return (
        '<section data-slop="hero" class="relative overflow-hidden bg-gradient-to-br '
        'from-purple-600 via-indigo-600 to-blue-600 text-white">'
        '<div class="mx-auto max-w-5xl px-6 py-24 text-center">'
        f'{badge_html}'
        '<h1 class="mt-6 bg-gradient-to-r from-white via-purple-200 to-blue-200 bg-clip-text '
        f'text-5xl font-extrabold tracking-tight text-transparent sm:text-6xl">{_e(headline)}</h1>'
        f'<p class="mx-auto mt-6 max-w-2xl text-lg text-purple-100">{_e(subheadline)}</p>'
        '<a data-cta href="#" class="mt-10 inline-block rounded-xl bg-white px-8 py-4 font-semibold '
        'text-purple-700 shadow-lg transition-transform duration-200 hover:scale-105">'
        f'{_e(cta)}</a>'
        '</div></section>'
    )


def _stats(fields: dict) -> str:
    items = _items(fields, "stats", {"value": 24, "label": 60}, 2, 4)
    cells = "".join(
        '<div><p class="bg-gradient-to-r from-purple-600 to-blue-600 bg-clip-text text-4xl '
        f'font-extrabold text-transparent">{_e(i["value"])}</p>'
        f'<p class="mt-2 text-sm text-slate-500 dark:text-slate-400">{_e(i["label"])}</p></div>'
        for i in items
    )
    return (
        '<section data-slop="stats" data-fade class="bg-slate-50 py-12 dark:bg-slate-900">'
        f'<div class="mx-auto grid max-w-5xl grid-cols-2 gap-8 px-6 text-center md:grid-cols-{len(items)}">'
        f'{cells}</div></section>'
    )


def _features(fields: dict) -> str:
    items = _items(fields, "features", {"emoji": 16, "title": 80, "text": 300}, 3, 3)
    heading = _heading(_text(fields, "headline", 120, "Powerful Features"), _optional(fields, "subheadline", 300))
    cards = "".join(
        '<div class="rounded-2xl bg-white p-8 shadow-xl shadow-purple-500/10 ring-1 ring-slate-100 '
        'transition hover:-translate-y-1 dark:bg-slate-800 dark:ring-slate-700">'
        f'<div class="text-4xl">{_e(i["emoji"])}</div>'
        f'<h3 class="mt-4 text-xl font-semibold">{_e(i["title"])}</h3>'
        f'<p class="mt-2 text-slate-600 dark:text-slate-400">{_e(i["text"])}</p></div>'
        for i in items
    )
    return (
        '<section id="features" data-slop="features" data-fade class="mx-auto max-w-5xl px-6 py-20">'
        f'{heading}<div class="mt-12 grid gap-8 md:grid-cols-3">{cards}</div></section>'
    )


def _testimonials(fields: dict) -> str:
    items = _items(fields, "testimonials", {"name": 60, "role": 80, "quote": 400}, 1, 4)
    heading = _heading(_text(fields, "headline", 120, "Loved by thousands of users"),
                       _optional(fields, "subheadline", 300))
    cards = "".join(
        '<figure class="rounded-2xl bg-white p-8 shadow-xl ring-1 ring-slate-100 '
        'dark:bg-slate-800 dark:ring-slate-700">'
        f'<blockquote class="text-slate-700 dark:text-slate-300">“{_e(i["quote"])}”</blockquote>'
        f'<figcaption class="mt-6"><p class="font-semibold">{_e(i["name"])}</p>'
        f'<p class="text-sm text-slate-500 dark:text-slate-400">{_e(i["role"])}</p></figcaption></figure>'
        for i in items
    )
    return (
        '<section data-slop="testimonials" data-fade class="bg-slate-50 py-20 dark:bg-slate-900">'
        f'<div class="mx-auto max-w-5xl px-6">{heading}'
        f'<div class="mt-12 grid gap-8 md:grid-cols-{min(len(items), 3)}">{cards}</div></div></section>'
    )


def _faq(fields: dict) -> str:
    items = _items(fields, "faq", {"q": 160, "a": 500}, 2, 8)
    heading = _heading(_text(fields, "headline", 120, "Frequently Asked Questions"),
                       _optional(fields, "subheadline", 300))
    rows = "".join(
        '<details class="group rounded-2xl bg-white p-6 shadow-md ring-1 ring-slate-100 '
        'dark:bg-slate-800 dark:ring-slate-700">'
        f'<summary class="cursor-pointer list-none font-semibold">{_e(i["q"])}</summary>'
        f'<p class="mt-3 text-slate-600 dark:text-slate-400">{_e(i["a"])}</p></details>'
        for i in items
    )
    return (
        '<section id="faq" data-slop="faq" data-fade class="mx-auto max-w-3xl px-6 py-20">'
        f'{heading}<div class="mt-12 space-y-4">{rows}</div></section>'
    )


def _cta(fields: dict) -> str:
    headline = _text(fields, "headline", 120, "Ready to get started?")
    text = _optional(fields, "text", 300)
    button = _text(fields, "button", 40, "Get Started")
    text_html = f'<p class="mx-auto mt-4 max-w-xl text-lg text-purple-100">{_e(text)}</p>' if text else ""
    return (
        '<section data-slop="cta" data-fade class="bg-gradient-to-r from-purple-600 to-blue-600 py-20 text-white">'
        '<div class="mx-auto max-w-3xl px-6 text-center">'
        f'<h2 class="text-3xl font-bold sm:text-4xl">{_e(headline)}</h2>{text_html}'
        '<a href="#" class="mt-8 inline-block rounded-xl bg-white px-8 py-4 font-semibold text-purple-700 '
        f'shadow-lg transition-transform duration-200 hover:scale-105">{_e(button)}</a></div></section>'
    )


def _footer(fields: dict) -> str:
    text = _text(fields, "text", 200)
    built = "" if "Built with" in text else (
        '<p class="mt-2">Built with ❤️</p>')
    return (
        '<footer data-slop="footer" class="bg-slate-900 py-10 text-center text-sm text-slate-400">'
        f'<p>{_e(text)}</p>{built}</footer>'
    )
