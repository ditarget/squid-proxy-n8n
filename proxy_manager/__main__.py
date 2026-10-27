import sys

from .supervisor import main


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--check"]):
        raise SystemExit("usage: python3 -m proxy_manager [--check]")
    raise SystemExit(main(check="--check" in sys.argv))
