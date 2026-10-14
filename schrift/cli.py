"""Command line: `schrift user create`, `schrift user reset`, `schrift scan`."""

import argparse
import getpass
import sys

from sqlalchemy import func, select

from schrift import security
from schrift.config import get_settings
from schrift.main import build_context, scan_all
from schrift.models import User


def _password(args) -> str:
    if args.password:
        return args.password
    if not sys.stdin.isatty():
        return sys.stdin.readline().strip()
    while True:
        first = getpass.getpass("Wachtwoord: ")
        if problem := security.password_problem(first):
            print(problem)
            continue
        if getpass.getpass("Nog een keer: ") == first:
            return first
        print("De wachtwoorden zijn niet gelijk.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="schrift")
    sub = parser.add_subparsers(dest="command", required=True)

    user = sub.add_parser("user", help="manage users").add_subparsers(dest="action", required=True)
    create = user.add_parser("create", help="create a user")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    create.add_argument("--password", help="read from stdin or prompt when omitted")
    create.add_argument(
        "--temporary",
        action="store_true",
        help="generate a temporary password that must be changed at first login",
    )
    reset = user.add_parser("reset", help="set a new password and turn off 2FA")
    reset.add_argument("username")
    reset.add_argument("--password")
    user.add_parser("list", help="list users")

    sub.add_parser("scan", help="index Markdown files changed outside Schrift")

    args = parser.parse_args(argv)
    ctx = build_context(get_settings())

    if args.command == "scan":
        scan_all(ctx)
        return 0

    with ctx.db.session() as session:
        if args.action == "list":
            for u in session.scalars(select(User).order_by(User.username)):
                flags = [f for f, on in (("admin", u.is_admin), ("2fa", u.totp_enabled)) if on]
                print(u.username, " ".join(flags))
            return 0
        existing = session.scalar(
            select(User).where(func.lower(User.username) == args.username.lower())
        )
        if args.action == "create":
            if existing:
                print(f"Gebruiker '{args.username}' bestaat al.", file=sys.stderr)
                return 1
            password = security.new_app_password() if args.temporary else _password(args)
            if problem := security.password_problem(password):
                print(problem, file=sys.stderr)
                return 1
            session.add(
                User(
                    username=args.username,
                    password_hash=security.hash_password(password),
                    is_admin=args.admin,
                    must_change_password=args.temporary,
                )
            )
            if args.temporary:
                print(f"Tijdelijk wachtwoord: {password}")
            print(
                f"Gebruiker '{args.username}' aangemaakt. Stel 2FA in bij de eerste keer inloggen."
            )
            return 0
        if args.action == "reset":
            if not existing:
                print(f"Gebruiker '{args.username}' bestaat niet.", file=sys.stderr)
                return 1
            password = _password(args)
            if problem := security.password_problem(password):
                print(problem, file=sys.stderr)
                return 1
            existing.password_hash = security.hash_password(password)
            existing.totp_enabled = False
            existing.totp_secret = None
            existing.recovery_codes = []
            existing.session_version += 1
            print("Wachtwoord gezet en 2FA uitgezet. Stel 2FA opnieuw in bij het inloggen.")
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
