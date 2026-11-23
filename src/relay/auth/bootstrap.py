import argparse
import sys
from getpass import getpass

from pydantic import SecretStr, ValidationError

from relay.auth.accounts import NewUser, create_user


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the initial Relay administrator")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--stdin", action="store_true", help="Read one temporary password line")
    args = parser.parse_args()
    password = (
        sys.stdin.readline().rstrip("\r\n") if args.stdin else getpass("Temporary password: ")
    )
    try:
        values = NewUser(username=args.username, password=SecretStr(password), role="admin")
    except ValidationError:
        raise SystemExit(
            "Use a valid username and a temporary password of 12–128 characters."
        ) from None
    if create_user(values, bootstrap=True) is None:
        raise SystemExit("A user already exists. Sign in with an existing administrator.")
    print(f"Initial administrator created: {values.username}. Change the password after sign-in.")


if __name__ == "__main__":
    main()
