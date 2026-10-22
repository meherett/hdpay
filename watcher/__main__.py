"""Run one watcher per chain:  python -m watcher --chain tron"""

import argparse
import logging
import os

from api.balances import verify_tokens
from api.chains import CHAINS
from . import make_scanner
from .core import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m watcher")
    # or HDPAY_CHAIN, so its docker image runs as is: docker run -e HDPAY_CHAIN=sepolia hdpay-watcher
    chain_key = os.environ.get("HDPAY_CHAIN") or None
    parser.add_argument("--chain", required=chain_key is None, default=chain_key, choices=list(CHAINS))
    args = parser.parse_args()
    if args.chain not in CHAINS:   # argparse does not check a default against the choices
        parser.error(f"HDPAY_CHAIN {args.chain!r} is not a chain; one of {', '.join(CHAINS)}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    chain = CHAINS[args.chain]
    # refuse to start on a token whose decimals in the chains file do not match its contract
    for note in verify_tokens(chain):
        logging.getLogger("hdpay.watcher").info("token %s", note)
    run(make_scanner(chain), make_scanner)


if __name__ == "__main__":
    main()
