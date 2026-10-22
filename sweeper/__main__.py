"""Run one sweeper per chain, on the host that holds the xprv:  python -m sweeper --chain tron"""

import argparse
import logging

from api.balances import verify_tokens
from api.chains import CHAINS
from . import make_sweeper
from .core import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sweeper")
    parser.add_argument("--chain", required=True, choices=list(CHAINS))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    chain = CHAINS[args.chain]
    # refuse to start on a token whose decimals in the chains file do not match its contract
    for note in verify_tokens(chain):
        logging.getLogger("hdpay.sweeper").info("token %s", note)
    run(make_sweeper(chain), make_sweeper)


if __name__ == "__main__":
    main()
