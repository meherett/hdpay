"""Run one sweeper per chain, on the host that holds the xprv:  python -m sweeper --chain tron"""

import argparse
import logging
import os

from api.balances import verify_tokens
from api.chain_sync import confirmed_tokens
from api.chains import CHAINS
from . import make_sweeper
from api.config import settings
from api.db import get_db
from .core import WrongMasterKey, check_master_key_matches, run


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
    log = logging.getLogger("hdpay.sweeper")
    # first of all the key: a root private key, and the one this database's projects were made with
    try:
        check_master_key_matches(get_db(), settings.root_xprv)
    except WrongMasterKey as exc:
        log.error("%s", exc)
        raise SystemExit(2) from None
    log.info("HDPAY_ROOT_XPRV is a root key, and the key of every project in database %r", settings.mongodb_db)
    # refuse to start on a token whose decimals in the chains file do not match its contract;
    # the tokens the database records as confirmed already are not asked again, only new or changed ones
    for note in verify_tokens(chain, confirmed_tokens(chain)):
        logging.getLogger("hdpay.sweeper").info("token %s", note)
    try:
        run(make_sweeper(chain), make_sweeper)
    except WrongMasterKey as exc:
        logging.getLogger("hdpay.sweeper").error("%s", exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
