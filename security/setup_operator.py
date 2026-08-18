"""
setup_operator.py -- CLI to register an operator credential for the
schedule approval gate (traffic_sim.py's launch modal).

Run with:  python -m security.setup_operator
       or: python security/setup_operator.py

operators.json (security/auth.py's DEFAULT_CREDENTIALS_PATH) is already
gitignored - this script is how a local credential file gets created in
the first place, not something the repository ships with populated.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from .auth import AuthError, OperatorAuth


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Register an operator credential for the schedule approval gate.")
    parser.add_argument("--username", help="operator username (prompted if omitted)")
    parser.add_argument("--force", action="store_true",
                         help="overwrite an existing operator with this username")
    args = parser.parse_args(argv)

    auth = OperatorAuth.load_or_create()

    username = args.username or input("Operator username: ").strip()
    if not username:
        print("Username must not be empty.", file=sys.stderr)
        return 1

    if auth.has_operator(username) and not args.force:
        print(f"Operator {username!r} already exists. Re-run with --force to overwrite.",
              file=sys.stderr)
        return 1

    password = getpass.getpass("Operator password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        return 1

    try:
        auth.register(username, password, overwrite=args.force)
    except AuthError as exc:
        print(f"Could not register operator: {exc}", file=sys.stderr)
        return 1

    auth.save()
    print(f"Operator {username!r} registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
