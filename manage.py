import logging
from flask import current_app
from flask.ext.script import Manager
from flask.ext.migrate import MigrateCommand
from decimal import Decimal
from collections import deque
import signal

from lincoln import create_app, db, coinserv
from lincoln.models import Block, Transaction, Output, Address

import time
import datetime
from lincoln.utils import parse_output_sript

manager = Manager(create_app)
manager.add_command('db', MigrateCommand)


@manager.command
def init_db():
    db.session.commit()
    db.drop_all()
    db.create_all()


@manager.command
def sync():

    # Kinda hacky, but simple & effective
    loop = [1]

    def handler(signum, frame):
        loop.remove(1)

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
                addr.transactions.append(tx_obj)
                out.address = addr
                db.session.flush()

                # Update address total in amount
                addr.total_in += out.amount

            if not tx.is_coinbase():
                for txin in tx.vin:
                    obj = Output.query.filter_by(
                        origin_tx_hash=txin.prevout.hash,
                        index=txin.prevout.n).one()
                    obj.spent_tx = tx_obj
                    tx_obj.total_in += obj.amount

                    # Update address total out amount
                    obj.address.total_out += obj.amount
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
