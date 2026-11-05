"""Generate SPARKVAULT_ADMIN_HASH:  python -m app.hashpw   (prompts; nothing is echoed or stored)."""
import getpass

from .auth import hash_password

if __name__ == "__main__":
    pw = getpass.getpass("Admin password: ")
    if len(pw) < 12:
        raise SystemExit("use at least 12 characters")
    if pw != getpass.getpass("Again: "):
        raise SystemExit("passwords differ")
    print(hash_password(pw))
