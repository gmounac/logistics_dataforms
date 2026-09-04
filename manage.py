"""User-account admin from the command line — the only way to create the first
account, and a fallback when nobody can log in.

    uv run manage.py add <username> <viewer|operator|admin>   # prompts for a password
    uv run manage.py passwd <username>                        # reset a password
    uv run manage.py role <username> <role>
    uv run manage.py enable <username>
    uv run manage.py disable <username>
    uv run manage.py list

Set YARD_DATABASE_URL to point at a database other than sqlite:///yard.db.
"""

import getpass
import os
import sys

from src.db import init_db, make_engine, make_session_factory
from src.enums import Role
from src.services import UserService, YardError

DB_URL = os.environ.get("YARD_DATABASE_URL", "sqlite:///yard.db")


def _prompt_password() -> str:
    p1 = getpass.getpass("New password: ")
    if len(p1) < UserService.MIN_PASSWORD_LEN:
        sys.exit(f"password must be at least {UserService.MIN_PASSWORD_LEN} characters")
    if p1 != getpass.getpass("Repeat: "):
        sys.exit("passwords did not match")
    return p1


def main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.exit(__doc__)

    engine = make_engine(DB_URL)
    init_db(engine)
    session = make_session_factory(engine)()
    users = UserService(session)
    cmd, *rest = argv

    try:
        if cmd == "list":
            rows = users.list_()
            if not rows:
                print("(no users yet — create one with:  uv run manage.py add <name> admin)")
            for u in rows:
                flag = "  DISABLED" if u.disabled else ""
                print(f"{u.username:20} {u.role.value:9}{flag}")

        elif cmd == "add" and len(rest) == 2:
            username, role = rest
            user = users.create(username=username, password=_prompt_password(), role=Role(role))
            print(f"created {user.username} ({user.role.value})")

        elif cmd == "passwd" and len(rest) == 1:
            user = _find(users, rest[0])
            users.update(user.id, password=_prompt_password())
            print(f"password updated for {user.username}")

        elif cmd == "role" and len(rest) == 2:
            user = _find(users, rest[0])
            users.update(user.id, role=Role(rest[1]))
            print(f"{user.username} is now {rest[1]}")

        elif cmd in ("enable", "disable") and len(rest) == 1:
            user = _find(users, rest[0])
            users.update(user.id, disabled=(cmd == "disable"))
            print(f"{user.username} {cmd}d")

        else:
            sys.exit(__doc__)
    except (YardError, ValueError) as e:
        sys.exit(f"error: {e}")
    finally:
        session.close()


def _find(users: UserService, username: str):
    for u in users.list_():
        if u.username == username.strip().lower():
            return u
    sys.exit(f"no user {username!r}")


if __name__ == "__main__":
    main(sys.argv[1:])
