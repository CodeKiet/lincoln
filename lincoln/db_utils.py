from _decimal import Decimal
from flask import current_app
import sqlalchemy
from lincoln import coinserv, db
from lincoln.models import Transaction, Output, Address
from lincoln.utils import parse_output_sript


def get_output_from_txin(txin, block_obj):
    """
    This utility takes a txin and queries the DB for it. If it fails to find
    one then it proceeds to run an RPC query to try and re-add it to the DB.

    If it fails to locate the output it'll raise an exception
    """

    out = None
    try:
        out = Output.query.filter_by(
            origin_tx_hash=txin.prevout.hash,
            index=txin.prevout.n).one()
    except sqlalchemy.orm.exc.NoResultFound:
        current_app.logger.warn(
            "Output with origin_tx_hash {} and index {} was not found! "
            "Attempting to grab the origin block and tx from the RPC to reindex"
            " it...".format(txin.prevout.hash, txin.prevout.n))
        try:
            tx_obj = Transaction.query.filter_by(txid=txin.prevout.hash).one()
        except sqlalchemy.orm.exc.NoResultFound:
            tx_obj = Transaction(block=block_obj,
                                 txid=txin.prevout.hash,
                                 total_in=0,
                                 total_out=0)
            db.session.add(tx_obj)
            db.session.flush()

        # grab the block from RPC
        block = coinserv.getblock(tx_obj.block.hash)

        block_tx = None
        for tx in block.vtx[:]:
            if tx.GetHash() == txin.prevout.hash:
                current_app.logger.debug("TX {} matched!".format(tx.GetHash()))
                block_tx = tx

        if block_tx:
            for i, txout in enumerate(block_tx.vout):
                out_dec = Decimal(txout.nValue) / 100000000
                try:
                    out = Output.query\
                        .filter_by(origin_tx_hash=block_tx.GetHash(),
                                   amount=out_dec).one()
                    out.index = i
                    db.session.flush()
                except sqlalchemy.orm.exc.NoResultFound:
                    # add the missing one
                    out = Output(origin_tx=tx_obj,
                                 index=i,
                                 amount=out_dec)
                    db.session.add(out)
                    current_app.logger.debug(
                        "Adding output {} to tx {}"
                        .format(out.__dict__, txin.prevout.hash))

                    tx_obj.total_out += out_dec
                    dest_address, out.type = parse_output_sript(txout)

                    if out.type != 3:
                        addr_version = current_app.config['currency'][out.type_str + '_address_version']
                    else:
                        continue

                    addr = Address.get_addr(dest_address, addr_version)
                    if not addr.first_seen_at:
                        addr.first_seen_at = tx_obj.block.ntime
                    out.address = addr
                    # Update address total in amount
                    addr.total_in += out.amount
                else:
                    current_app.logger.info("Located output {}".format(out))
        else:
            current_app.logger.info("Transaction not located in block {}!"
                                    .format(tx_obj.block.hash))

    if not out:
        raise sqlalchemy.orm.exc.NoResultFound(
            "Unable to locate Output with origin_tx_hash {} and index {}!"
            .format(txin.prevout.hash, txin.prevout.n))
    else:
        db.session.flush()
        return out