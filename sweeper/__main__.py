"""Run one sweeper per chain, on the host that holds the xprv:  python -m sweeper --chain tron"""

import argparse
import logging
import os

from api.balances import verify_tokens
from api.chain_sync import confirmed_tokens
from api.chains import CHAINS
from . import make_sweeper
from .core import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sweeper")
    # or HDPAY_CHAIN, so its docker image runs as is: docker run -e HDPAY_CHAIN=sepolia hdpay-sweeper
    chain_key = os.environ.get("HDPAY_CHAIN") or None
    parser.add_argument("--chain", required=chain_key is None, default=chain_key, choices=list(CHAINS))
    args = parser.parse_args()
    if args.chain not in CHAINS:   # argparse does not check a default against the choices
        parser.error(f"HDPAY_CHAIN {args.chain!r} is not a chain; one of {', '.join(CHAINS)}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    chain = CHAINS[args.chain]
    # refuse to start on a token whose decimals in the chains file do not match its contract;
    # the tokens the database records as confirmed already are not asked again, only new or changed ones
    for note in verify_tokens(chain, confirmed_tokens(chain)):
        logging.getLogger("hdpay.sweeper").info("token %s", note)
    run(make_sweeper(chain), make_sweeper)


if __name__ == "__main__":
    main()
