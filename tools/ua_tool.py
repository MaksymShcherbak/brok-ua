#!/usr/bin/env python3
"""BROK: The InvestiGator -- Ukrainian translation workflow helper.

Deterministic utilities so mechanical checks cost zero model tokens.

Exported translation files use this shape:

    # LANGUAGE: UKR
    #-------------------------------
    #=LABEL
    #-------------------------------
    # Speaker
    ### English original line
    SOME_KEY=Ukrainian translation

Line rules:
  * lines starting with '#'    -> comment (never edit)
  * lines starting with '### ' -> the English original ('###' + text)
  * KEY=value                  -> translation slot; empty value = untranslated

Commands
  stats FILE [FILE ...]        key counts (total / translated / untranslated)
  untranslated FILE [FILE ...] list empty slots with their English original
  check BEFORE AFTER           prove a rewrite kept keys/comments/order/EOL/BOM
  speakers FILE                best-effort list of speakers in the file
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

KEY_RE = re.compile(r"^([A-Za-z0-9_.\-]+)=(.*)$")
SPEAKER_RE = re.compile(r"^#\s+([A-Z][A-Za-z.']*(?:[ ][A-Za-z.']+){0,2})\s*$")


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def has_bom(path: Path) -> bool:
    return read_bytes(path)[:3] == b"\xef\xbb\xbf"


def eol_style(path: Path) -> str:
    data = read_bytes(path)
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return "mixed"
    return "crlf" if crlf else "lf"


def read_text(path: Path) -> str:
    return read_bytes(path).decode("utf-8-sig", errors="replace")


def classify(line: str):
    if not line.strip():
        return ("blank", None)
    if line.startswith("###"):
        return ("orig", line[3:].strip())
    if line.startswith("#"):
        return ("comment", line)
    m = KEY_RE.match(line)
    if m:
        return ("kv", (m.group(1), m.group(2)))
    return ("other", line)


def iter_entries(path: Path):
    for i, line in enumerate(read_text(path).splitlines(), 1):
        kind, payload = classify(line)
        yield i, kind, payload


def cmd_stats(args):
    rc = 0
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            print(f"! not found: {f}", file=sys.stderr)
            rc = 1
            continue
        total = trans = 0
        for _, kind, payload in iter_entries(p):
            if kind == "kv":
                total += 1
                if payload[1].strip():
                    trans += 1
        print(f"{p.name}: {total} keys | {trans} translated | {total - trans} untranslated")
    return rc


def cmd_untranslated(args):
    rc = 0
    for f in args.files:
        p = Path(f)
        if not p.is_file():
            print(f"! not found: {f}", file=sys.stderr)
            rc = 1
            continue
        pending, last_orig = [], ""
        for i, kind, payload in iter_entries(p):
            if kind == "orig":
                last_orig = payload
            elif kind == "kv" and not payload[1].strip():
                pending.append((i, payload[0], last_orig))
        print(f"### {p.name}: {len(pending)} untranslated")
        for line, key, orig in pending:
            print(f"  L{line:<5} {key}   <- {orig}")
    return rc


def _signature(path: Path):
    keys, comments = [], []
    for _, kind, payload in iter_entries(path):
        if kind == "kv":
            keys.append(payload[0])
        elif kind in ("orig", "comment"):
            comments.append((kind, payload))
    return keys, comments


def cmd_check(args):
    before, after = Path(args.before), Path(args.after)
    for p in (before, after):
        if not p.is_file():
            print(f"! not found: {p}", file=sys.stderr)
            return 1
    ok = True
    kb, cb = _signature(before)
    ka, ca = _signature(after)
    if kb != ka:
        ok = False
        print("FAIL: key sequence changed")
        if Counter(kb) != Counter(ka):
            for label, diff in (("missing", Counter(kb) - Counter(ka)),
                                ("extra", Counter(ka) - Counter(kb))):
                if diff:
                    print(f"  {label}: {', '.join(sorted(diff))}")
        else:
            print("  (same keys, different order)")
    if cb != ca:
        ok = False
        print("FAIL: comment/original lines changed")
        for i in range(max(len(cb), len(ca))):
            a = cb[i] if i < len(cb) else None
            b = ca[i] if i < len(ca) else None
            if a != b:
                print(f"  first diff at comment #{i}: {a!r} -> {b!r}")
                break
    if has_bom(before) != has_bom(after):
        ok = False
        print(f"FAIL: BOM changed ({has_bom(before)} -> {has_bom(after)})")
    if eol_style(before) != eol_style(after):
        ok = False
        print(f"FAIL: line endings changed ({eol_style(before)} -> {eol_style(after)})")
    print(("OK: " if ok else "PROBLEMS in ")
          + f"{after.name} vs {before.name} ({len(ka)} keys)")
    return 0 if ok else 2


def cmd_normalize(args):
    p = Path(args.file)
    if not p.is_file():
        print(f"! not found: {p}", file=sys.stderr)
        return 1
    ref = Path(args.like) if args.like else None
    if ref and not ref.is_file():
        print(f"! not found: {ref}", file=sys.stderr)
        return 1
    text = read_text(p)  # strips BOM
    want_bom = has_bom(ref) if ref else has_bom(p)
    want_eol = eol_style(ref) if ref else eol_style(p)
    if want_eol == "mixed":
        want_eol = "crlf"
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if want_eol == "crlf":
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if want_bom:
        data = b"\xef\xbb\xbf" + data
    p.write_bytes(data)
    print(f"normalized {p.name}: bom={want_bom} eol={want_eol}")
    return 0


def cmd_speakers(args):
    p = Path(args.file)
    if not p.is_file():
        print(f"! not found: {p}", file=sys.stderr)
        return 1
    seen = []
    for _, kind, payload in iter_entries(p):
        if kind == "comment":
            m = SPEAKER_RE.match(payload)
            if m:
                name = m.group(1).strip()
                if name not in seen:
                    seen.append(name)
    print(f"{p.name}: {', '.join(seen) if seen else '(none detected)'}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("untranslated")
    s.add_argument("files", nargs="+")
    s.set_defaults(fn=cmd_untranslated)

    s = sub.add_parser("check")
    s.add_argument("before")
    s.add_argument("after")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("normalize")
    s.add_argument("file")
    s.add_argument("--like", default=None)
    s.set_defaults(fn=cmd_normalize)

    s = sub.add_parser("speakers")
    s.add_argument("file")
    s.set_defaults(fn=cmd_speakers)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
