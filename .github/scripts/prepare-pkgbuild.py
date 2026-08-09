#!/usr/bin/env python3
import argparse
from pathlib import Path
import re


def replace_once(data, old, new, what):
    if new in data:
        return data
    if data.count(old) != 1:
        raise SystemExit(f"PKGBUILD: expected one anchor for {what}")
    return data.replace(old, new, 1)


def patch_pkgbuild(path):
    data = path.read_text()

    anchor = ': "${_build_debug:=no}"\n'
    options = anchor + """

### Keep runtime optimization during release builds in CI
: "${_ci_fast_build:=yes}"

### Reduce compiler debug metadata without changing runtime optimization
: "${_reduced_debug_info:=no}"
"""
    data = replace_once(data, anchor, options, "CI build options")

    anchor = 'pkgbase="linux-$_pkgsuffix"\n'
    suffix = """# TSC directsync kernel remains installable beside stock CachyOS kernel.
_pkgsuffix=cachyos-tsc
""" + anchor
    data = replace_once(data, anchor, suffix, "package suffix")

    if "_upstream_pkgrel=" not in data:
        data, count = re.subn(
            r"^pkgrel=([0-9]+)$",
            r'_upstream_pkgrel=\1\npkgrel="${_tagrel}.${_upstream_pkgrel}"',
            data,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise SystemExit("PKGBUILD: expected one numeric pkgrel")

    anchor = '    echo "Setting config..."\n'
    patcher = """    echo "Applying TSC directsync forward-port..."
    python "${startdir}/tsc-directsync-7.1.py" .

""" + anchor
    data = replace_once(data, anchor, patcher, "TSC patcher")

    data = replace_once(
        data,
        "    if _is_ci_build; then\n",
        '    if _is_ci_build && [ "$_ci_fast_build" = "yes" ]; then\n',
        "CI optimization condition",
    )

    anchor = """            -e DEBUG_INFO_REDUCED
    fi

    ### Enable bbr3
"""
    reduced_debug = """            -e DEBUG_INFO_REDUCED
    fi

    if [ "$_reduced_debug_info" = "yes" ]; then
        scripts/config -e DEBUG_INFO_REDUCED
    fi

    ### Enable bbr3
"""
    data = replace_once(data, anchor, reduced_debug, "reduced debug config")

    path.write_text(data)


def package_version(data):
    values = {}
    for name in ("_major", "_minor", "_tagrel", "_upstream_pkgrel"):
        match = re.search(rf"^{re.escape(name)}=([^\n#]+)", data, re.MULTILINE)
        if not match:
            raise SystemExit(f"PKGBUILD: missing {name}")
        values[name] = match.group(1).strip().strip("'\"")
    return (
        f"{values['_major']}.{values['_minor']}"
        f"-{values['_tagrel']}.{values['_upstream_pkgrel']}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--print-version", action="store_true")
    args = parser.parse_args()

    patch_pkgbuild(args.path)
    if args.print_version:
        print(package_version(args.path.read_text()))


if __name__ == "__main__":
    main()
