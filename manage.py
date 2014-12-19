import logging
from flask import current_app
from flask.ext.script import Manager
from flask.ext.migrate import MigrateCommand
from decimal import Decimal
from collections import deque

from lincoln import create_app, db, coinserv
from lincoln.models import Block, Transaction, Output, Address

import bitcoin.core.script as op
import bitcoin.core.serialize as serialize

import time
import datetime

manager = Manager(create_app)
manager.add_command('db', MigrateCommand)


@manager.command
def init_db():
    db.session.commit()
    db.drop_all()
    db.create_all()


@manager.command
def sync():
    # Get the most recent block in our database
    highest = Block.query.order_by(Block.height.desc()).first()
    if highest:
        highest_hash = coinserv.getblockhash(highest.height)

    # This means the coinserver and local index are on different chains
    #if highest_hash != highest.hash:

    server_height = coinserv.getinfo()['blocks']
    server_hash = coinserv.getblockhash(server_height)

    block_times = deque([], maxlen=1000)
    while True:
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

                # Sloppy as hell destination address checking
                # ------------------------------------------------
                scr = []
                try:
                    scr = list(txout.scriptPubKey)
                except op.CScriptTruncatedPushDataError:
                    pass

                out = Output(origin_tx=tx_obj,
                             index=i,
                             amount=out_dec)
                db.session.add(out)

                dest_address = None
                # pay-to-pubkey-hash
                if (len(scr) == 5 and
                        scr[0] == op.OP_DUP and
                        scr[1] == op.OP_HASH160 and
                        scr[3] == op.OP_EQUALVERIFY and
                        scr[4] == op.OP_CHECKSIG):
                    out.type = 1
                    dest_address = scr[2]
                elif (len(scr) == 3 and
                      scr[0] == op.OP_HASH160 and
                      scr[2] == op.OP_EQUAL):
                    out.type = 0
                    dest_address = scr[1]
                elif len(scr) == 2 and scr[1] == op.OP_CHECKSIG:
                    out.type = 2
                    dest_address = serialize.Hash160(scr[0])
                else:
                    out.type = 3
                    current_app.logger.warn("Unrecognized script {}"
                                            .format(scr))

                if out.type != 3:
                    addr_version = current_app.config['currency'][out.type_str + '_address_version']
                else:
                    continue

                # lookup address object matching dest_addr
                addr = Address.query.filter_by(hash=dest_address,
                                               version=addr_version).first()

                if not addr:
                    addr = Address(hash=dest_address,
                                   version=addr_version,
                                   currency=current_app.config['currency']['code'])
                    db.session.add(addr)

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
        if curr_height % interval == 0:
            # Display progress information
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
