import logging
import decorator
from flask import current_app
from flask.ext.script import Manager
from flask.ext.migrate import MigrateCommand
from decimal import Decimal
from collections import deque
import signal
import sqlalchemy
import gevent
from gevent.queue import Queue

from lincoln import create_app, db, coinserv
from lincoln.models import Block, Transaction, Output, Address

import time
import datetime
from lincoln.utils import parse_output_sript

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


@manager.command
@crontab
def delete_highest_block():
    block = Block.query.order_by(Block.height.desc()).first()
    block.remove()
    db.session.commit()


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

    current_app.logger.debug("Database height: {}, RPC height: {}"
                             .format(highest.height, server_height))
    if highest and highest.height >= server_height:
        current_app.logger.info("Already sync'd up!")
        exit(0)

    # Check for forks, but only if we're relatively sync'd up
    if highest and server_height <= highest.height + 150:
        server_highest_hash = coinserv.getblockhash(highest.height)
        if server_highest_hash != highest.hash:
            # Delete blocks until we find a common ancestor
            while True:
                second_highest = Block.query.filter_by(height=highest.height - 1).one()
                highest.remove()

                server_prev_hash = coinserv.getblockhash(second_highest.height)
                if server_prev_hash == second_highest.hash:
                    db.session.commit()
                    break
                else:
                    highest = second_highest

    block_times = deque([], maxlen=1000)
    curr_height = highest.height if highest else 0
    while loop:

        t = time.time()
        if curr_height > server_height:
            break
        else:
            curr_height += 1
        curr_hash = coinserv.getblockhash(curr_height)

        # Don't flood the RPC server if it is remote
        if current_app.config['coinserv'].get('remote', False):
            time.sleep(0.2)

        block = coinserv.getblock(curr_hash)
        block_obj = Block(hash=block.GetHash(),
                          height=curr_height,
                          ntime=datetime.datetime.utcfromtimestamp(block.nTime),
                          orphan=False,
                          difficulty=block.difficulty,
                          algo=current_app.config['algo']['display'],
                          currency=current_app.config['currency']['code'])
        current_app.logger.debug("Syncing block {}".format(block_obj))
        db.session.add(block_obj)

        # all TX's in block are connectable; index
        for tx in block.vtx:
            tx_obj = Transaction.get(tx.GetHash(), block_obj)

            for i, tx_output in enumerate(tx.vout):
                output_amt = Decimal(tx_output.nValue) / 100000000
                tx_obj.total_out += output_amt

                output_obj = Output.get_output(tx.GetHash(), output_amt, i)
                dest_address, output_obj.type = parse_output_sript(tx_output)

                if output_obj.type != 3:
                    addr_version = current_app.config['currency'][output_obj.type_str + '_address_version']
                else:
                    continue

                addr = Address.get_addr(dest_address, addr_version)
                if not addr.first_seen_at:
                    addr.first_seen_at = block_obj.ntime

                output_obj.address = addr
                # Update address total in amount
                addr.total_in += output_obj.amount

            if not tx.is_coinbase():
                for tx_input in tx.vin:
                    input = Output.get_input(tx_input.prevout.hash, tx_input.prevout.n)
                    input.spend_tx = tx_obj
                    tx_obj.total_in += input.amount

                    # Update address total out amount
                    if input.address:
                        input.address.total_out += input.amount
            else:
                tx_obj.coinbase = True

            # for tx in tx.vin:
            block_obj.total_in += tx_obj.total_in
            block_obj.total_out += tx_obj.total_out

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
