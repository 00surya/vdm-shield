"""Set the local operator password without storing plaintext."""
import getpass
from pathlib import Path
import os
from werkzeug.security import generate_password_hash

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "data"
    root.mkdir(exist_ok=True)
    password = getpass.getpass("New operator password (at least 12 characters): ")
    if len(password) < 12 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords must match and contain at least 12 characters.")
    temporary = root / "access.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(generate_password_hash(password))
    temporary.replace(root / "access.hash")
    print("Password saved. Username: operator. Restart VMD to activate.")
