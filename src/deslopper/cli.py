import argparse
import sys
from pathlib import Path

from deslopper.cache import Cache
from deslopper.extras import component_name, page_extras
from deslopper.providers import DEFAULT_PROVIDER, PROVIDERS, ProviderError, make_provider
from deslopper.receipts import append_entries, count_tones, load_tells, load_tones, summarize
from deslopper.sweep import (
    SweepError,
    backup_original,
    copy_page_assets,
    generate_tests,
    kind_of,
    prepare,
    read_source,
    sweep_directory,
    sweep_source,
    write_output,
)
from deslopper.web.ultra import write_drawings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="de-slopper",
        description="Turns messy human code into professional code.",
    )
    parser.add_argument("path", help="a .py, .html or .md file, or a directory of them")
    parser.add_argument("-o", "--output", help="output file or directory (required for a directory)")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default=DEFAULT_PROVIDER)
    parser.add_argument("--receipt", help="where to write the JSON Lines receipt")
    parser.add_argument("--no-tests", action="store_true", help="do not write test files")
    parser.add_argument("--react", action="store_true", help="also write LandingPage.tsx for each page")
    parser.add_argument("--ultra", action="store_true",
                        help="also redraw every image of a page as an SVG the model draws (an experiment)")
    parser.add_argument("--screenshot", action="store_true",
                        help="also write before and after PNGs of each page (needs the screenshot extra)")
    return parser


def force_utf8() -> None:
    # Windows consoles default to a legacy code page and crash on emoji.
    # Newlines on stdout are left alone so file contents pass through exactly.
    sys.stdout.reconfigure(encoding="utf-8", newline="")
    sys.stderr.reconfigure(encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        raise SweepError(f"expected an existing path, got {args.path}")

    provider = make_provider(args.provider)
    cache = Cache()
    out = Path(args.output) if args.output else None
    receipt = Path(args.receipt) if args.receipt else None
    status = 0
    shown = ""

    if path.is_dir():
        if out is None:
            raise SweepError("expected -o for a directory run")
        entries = sweep_directory(path, out, provider, cache, receipt, tests=not args.no_tests,
                                  react=args.react, screenshot=args.screenshot, ultra=args.ultra)
    else:
        kind = kind_of(path)
        if kind is None:
            raise SweepError(f"expected a .py, .html or .md file, got {path.name}")
        if out is None and (args.react or args.screenshot or args.ultra):
            flag = "--react" if args.react else "--screenshot" if args.screenshot else "--ultra"
            raise SweepError(f"expected -o with {flag}")
        source = read_source(path)
        page_kind, text = prepare(kind, source, path.name)
        result = sweep_source(page_kind, text, path.name, provider, cache, origin=path,
                              ultra=args.ultra, art_dir=out.parent if out else None)
        entries = list(result.entries)
        if out is not None:
            receipt = receipt or out.with_name(out.name + ".receipt.jsonl")
        if result.output is None:
            status = 1
        elif out is not None:
            originals = frozenset({path.resolve()})
            entries += backup_original(out, path.name, originals)
            write_output(out, result.output)
            write_drawings(out.parent, result.drawings)
            entries += copy_page_assets(page_kind, path.name, result.kept or result.output, path.parent,
                                        out.parent)
            if not args.no_tests:
                test_path = out.with_name(f"test_{out.stem}.py")
                entries += generate_tests(kind, path.name, (out.stem,), out, test_path,
                                          out.parent, provider, cache, originals)
            entries += page_extras(page_kind, path.name, path, text, result.output, out, args.react,
                                   args.screenshot, component_name(out.stem, single=True))
        else:
            sys.stdout.write(result.output)
            sys.stdout.flush()
            shown = result.output
        if receipt is not None:
            receipt.unlink(missing_ok=True)
            append_entries(receipt, entries)

    tones = count_tones(read_prose(out, shown), load_tones())
    print(summarize(entries, load_tells(), tones), file=sys.stderr)
    return status


def read_prose(out: Path | None, shown: str) -> str:
    # What the tone tells are counted in: the swept files and their tests,
    # or the text that went to stdout when nothing was written.
    if out is None or not out.exists():
        return shown
    if out.is_dir():
        files = [p for p in out.rglob("*") if p.suffix in (".py", ".html")]
    else:
        files = [out, out.with_name(f"test_{out.stem}.py")]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files if p.is_file())


def main(argv: list[str] | None = None) -> int:
    force_utf8()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (SweepError, ProviderError) as err:
        print(f"de-slopper: {err}", file=sys.stderr)
        return 2
