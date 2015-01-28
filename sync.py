import datetime
import greenlet
import time
import signal
import yaml
import logging
import sqlalchemy as sa
import guv
guv.monkey_patch()

from _decimal import Decimal
from collections import deque
from sqlalchemy.orm import sessionmaker
from bitcoin.rpc import Proxy

from lincoln.models import Block, Transaction, Output, Address
from lincoln.utils import parse_output_sript, build_logger_from_config

# Setup
config = yaml.load(open("config.yml"))
logger = build_logger_from_config(config, "INFO")
coinserv = Proxy(
    "http://{0}:{1}@{2}:{3}/"
    .format(config['coinserv']['username'],
            config['coinserv']['password'],
            config['coinserv']['address'],
            config['coinserv']['port']))

engine = sa.create_engine(config['SQLALCHEMY_DATABASE_URI'],
                          echo=config.get('log_level') == "DEBUG")
db = sessionmaker(bind=engine)
db.session = db()
# Hack for flask in the env
db.session._model_changes = {}

blocks = guv.queue.PriorityQueue(maxsize=500)
pool = guv.greenpool.GreenPool(size=100)

loop = [1]
server_height = coinserv.getblockcount()
block_times = deque([], maxlen=1000)


def queue_getter():

    while loop:
        t = time.time()
        curr_height, rpc_block = blocks.get()
        process_block(curr_height, rpc_block)
        blocks.task_done()

        block_times.append(time.time() - t)
        interval = 1 if config.get('log_level', logging.DEBUG) == logging.DEBUG else 100
        # Display progress information
        if curr_height % interval == 0:
            time_per = sum(block_times) / len(block_times)
            time_remain = datetime.timedelta(
                seconds=time_per * (server_height - curr_height))
            logger.info(
                "{:,}/{:,} {} estimated to catchup"
                .format(curr_height, server_height, time_remain))
    else:
        raise greenlet.GreenletExit()


def queue_setter(curr_height):
    # query the rpc
    block_hash = coinserv.getblockhash(curr_height)
    block_obj = coinserv.getblock(block_hash)
    blocks.put((curr_height, block_obj))

    logger.info("Queue size: {}".format(blocks.qsize()))
    # Don't flood the RPC server if it is remote
    if config['coinserv'].get('remote', False):
        time.sleep(0.2)


def sync():

    def sig_handler(signum, frame):
        if signum == signal.SIGINT:
            # Fist SIGINT exits after next loop finishes
            if 1 in loop:
                logger.info("Caught exit signal, cleaning up...")
                loop.remove(1)
            # Second SIGINT exits immediately
            else:
                exit(0)

    signal.signal(signal.SIGINT, sig_handler)

    # Get the most recent block in our database
    highest = db.session.query(Block).order_by(Block.height.desc()).first()
    logger.info("Database height: {}, RPC height: {}"
                .format(highest.height, server_height))

    if highest and highest.height >= server_height:
        logger.info("Already sync'd up!")
        exit(0)

    # Check for forks, but only if we're relatively sync'd up
    if highest and server_height <= highest.height + 150:
        server_highest_hash = coinserv.getblockhash(highest.height)
        if server_highest_hash != highest.hash:
            # Delete blocks until we find a common ancestor
            while True:
                second_highest = db.session.query(Block)\
                    .filter_by(height=highest.height - 1).one()
                highest.remove()

                server_prev_hash = coinserv.getblockhash(second_highest.height)
                if server_prev_hash == second_highest.hash:
                    db.session.commit()
                    break
                else:
                    highest = second_highest

    curr_height = highest.height + 1 if highest else 1

    getter = pool.spawn(queue_getter)

    while curr_height < server_height and loop:
        setter = pool.spawn(queue_setter, curr_height)
        setter.wait()
        curr_height += 1

        # Async code - requires thread safe HTTP client
        # thread_heights = [curr_height + i for i in range(20)]
        # for height in thread_heights:
        #    pool.spawn(queue_setter, height)
        # pool.waitall()
        # curr_height += 20

    blocks.join()
    try:
        pool.waitall()
    finally:
        if pool.running() > 0:
            logger.info("Killing {} running greenlets".format(pool.running()))
            getter.kill()
        logger.info("Exit")
        logger.info("=" * 80)


def get_tx(tx_hash, block_obj):
    # We don't want to have to do a rollback, so query for the TX first
    try:
        tx_obj = db.session.query(Transaction).filter_by(txid=tx_hash).one()
        tx_obj.block = block_obj
        tx_obj.total_out = 0
        tx_obj.total_in = 0
        logger.info("Found old tx & overwrote {}".format(tx_obj))
    except sa.orm.exc.NoResultFound:
        tx_obj = Transaction(block=block_obj, txid=tx_hash)
        db.session.add(tx_obj)
        logger.info("Found new tx {}".format(tx_obj))
    db.session.flush()
    return tx_obj


def process_block(curr_height, rpc_block):
        block_obj = Block(hash=rpc_block.GetHash(),
                          height=curr_height,
                          ntime=datetime.datetime.utcfromtimestamp(rpc_block.nTime),
                          orphan=False,
                          difficulty=rpc_block.difficulty,
                          algo=config['algo']['display'],
                          currency=config['currency']['code'])
        logger.info("Syncing block {}".format(block_obj))
        db.session.add(block_obj)

        # all TX's in block are connectable; index
        for tx in rpc_block.vtx:
            tx_obj = get_tx(tx.GetHash(), block_obj)

            for i, tx_output in enumerate(tx.vout):
                output_amt = Decimal(tx_output.nValue) / 100000000
                tx_obj.total_out += output_amt

                output_obj = Output.get_output(tx.GetHash(), output_amt, i, db=db)
                dest_address, output_obj.type = parse_output_sript(tx_output)

                if output_obj.type != 3:
                    addr_version = config['currency'][output_obj.type_str + '_address_version']
                else:
                    continue

                addr = Address.get_addr(dest_address, addr_version,
                                        config['currency']['code'], db=db)
                if not addr.first_seen_at:
                    addr.first_seen_at = block_obj.ntime

                output_obj.address = addr
                # Update address total in amount
                addr.total_in += output_obj.amount

            if not tx.is_coinbase():
                for tx_input in tx.vin:
                    input = Output.get_input(tx_input.prevout.hash,
                                             tx_input.prevout.n, db=db)
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

if __name__ == '__main__':
    sync()