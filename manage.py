import logging
import decorator
from flask import current_app
from flask.ext.script import Manager
from flask.ext.migrate import MigrateCommand
from decimal import Decimal
from collections import deque
import signal
import sqlalchemy

from lincoln import create_app, db, coinserv
from lincoln.models import Block, Transaction, Output, Address

import time
import datetime
from lincoln.utils import parse_output_sript
from lincoln.db_utils import get_output_from_txin

manager = Manager(create_app)
manager.add_command('db', MigrateCommand)


@decorator.decorator
def crontab(func, *args, **kwargs):
    """ Handles rolling back SQLAlchemy exceptions to prevent breaking the
    connection for the whole scheduler. Also records timing information into
    the cache """

    res = None
    try:
        res = func(*args, **kwargs)
    except sqlalchemy.exc.SQLAlchemyError as e:
        current_app.logger.error("SQLAlchemyError occurred, rolling back: {}".
                                 format(e), exc_info=True)
        db.session.rollback()
    except Exception:
        current_app.logger.error("Unhandled exception in {}"
                                 .format(func.__name__), exc_info=True)
        raise

    return res


@manager.command
@crontab
def init_db():
    db.session.commit()
    db.drop_all()
    db.create_all()


# Don't use - likely broken
# @manager.command
# @crontab
# def delete_highest_block():
#     block = Block.query.order_by(Block.height.desc()).first()
#     for tx in block.transactions:
#         # Reverse spent TX output changes
#         for stxo in tx.origin_txs[:]:
#             if stxo.spend_tx_id:
#                 tx.origin_txs.remove(stxo)
#             if stxo.address:
#                 stxo.address.total_in -= stxo.amount
#         # Reverse unspent TX output changes & drop
#         for utxo in tx.spent_txs:
#             utxo.address.total_out -= utxo.amount
#             db.session.delete(utxo)
#         db.session.delete(tx)
#     db.session.delete(block)
#     db.session.commit()

@manager.command
@crontab
def sync():

    # Kinda hacky, but simple & effective way to break loop on SIGINT
    loop = [1]

    def handler(signum, frame):
        # Fist SIGINT exits after next loop finishes
        if 1 in loop:
            loop.remove(1)
        # Second SIGINT exits immediately
        else:
            exit(0)
    signal.signal(signal.SIGINT, handler)

    # Get the most recent block in our database
    highest = Block.query.order_by(Block.height.desc()).first()
    server_height = coinserv.getblockcount()

    # Check for forks, but only if we're relatively sync'd up
    if highest and server_height <= highest.height + 150:
        server_highest_hash = coinserv.getblockhash(highest.height)
        if server_highest_hash != highest.hash:
            # Delete blocks until we find a common ancestor
            while True:
                prev_height = highest.height - 1
                second_highest = Block.query.filter_by(height=prev_height).one()
                server_prev_hash = coinserv.getblockhash(second_highest.height)
                db.session.delete(highest)
                if server_prev_hash == second_highest.hash:
                    db.session.commit()
                    break
                else:
                    highest = second_highest


    block_times = deque([], maxlen=1000)
    while loop:

        # Don't flood the RPC server if it is remote
        if current_app.config['coinserv'].get('remote', False):
            time.sleep(1)

        t = time.time()
        if not highest:
            curr_height = 0
        else:
            curr_height = highest.height + 1

        if curr_height > server_height:
            break
        else:
            curr_hash = coinserv.getblockhash(curr_height)

        block = coinserv.getblock(curr_hash)
        block_obj = Block(hash=block.GetHash(),
                          height=curr_height,
                          ntime=datetime.datetime.utcfromtimestamp(block.nTime),
                          orphan=False,
                          total_in=0,
                          total_out=0,
                          difficulty=block.difficulty,
                          algo=current_app.config['algo']['display'],
                          currency=current_app.config['currency']['code'])
        current_app.logger.debug(
            "Syncing block {}".format(block_obj))
        db.session.add(block_obj)

        # all TX's in block are connectable; index
        for tx in block.vtx:
            tx_obj = Transaction(block=block_obj,
                                 txid=tx.GetHash(),
                                 total_in=0,
                                 total_out=0)
            db.session.add(tx_obj)
            current_app.logger.debug("Found new tx {}".format(tx_obj))

            for i, txout in enumerate(tx.vout):
                out_dec = Decimal(txout.nValue) / 100000000
                tx_obj.total_out += out_dec

                out = Output(origin_tx=tx_obj,
                             index=i,
                             amount=out_dec)
                db.session.add(out)

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

            db.session.flush()

            if not tx.is_coinbase():
                for txin in tx.vin:
                    output = get_output_from_txin(txin)
                    output.spent_tx = tx_obj
                    tx_obj.total_in += output.amount

                    # Update address total out amount
                    output.address.total_out += output.amount
            else:
                tx_obj.coinbase = True

            # for tx in tx.vin:
            block_obj.total_in += tx_obj.total_in
            block_obj.total_out += tx_obj.total_out

        highest = block_obj
        db.session.commit()

        block_times.append(time.time() - t)
        interval = 1 if current_app.log_level == logging.DEBUG else 100
        # Display progress information
        if curr_height % interval == 0:
            time_per = sum(block_times) / len(block_times)
            time_remain = datetime.timedelta(
                seconds=time_per * (server_height - curr_height))
            current_app.logger.info(
                "{:,}/{:,} {} estimated to catchup"
                .format(curr_height, server_height, time_remain))


manager.add_option('-c', '--config', default='/config.yml')
manager.add_option('-l', '--log-level',
                   choices=['DEBUG', 'INFO', 'WARN', 'ERROR'], default='INFO')

if __name__ == "__main__":
    manager.run()
