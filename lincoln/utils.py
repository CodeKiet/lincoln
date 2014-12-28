from bitcoin.core import serialize
from flask import current_app
import bitcoin.core.script as op
import time


class Benchmark(object):
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.start = time.time()

    def __exit__(self, ty, val, tb):
        end = time.time()
        current_app.logger.info("BENCHMARK: {} in {}"
                                .format(self.name, time_format(end - self.start)))
        return False


def time_format(seconds):
    # microseconds
    if seconds <= 1.0e-3:
        return "{:,.4f} us".format(seconds * 1000000.0)
    if seconds <= 1.0:
        return "{:,.4f} ms".format(seconds * 1000.0)
    return "{:,.4f} sec".format(seconds)


def get_int_from_str(str):
    """
    Takes a string, convert it to an int or returns False
    """
    try:
        integer = int(str.replace(',', ''))
    except ValueError:
        return False
    else:
        return integer


def parse_output_sript(txout):
    script_type = 3  # Defaults to 'unknown' script type
    dest_address = None

    # Sloppy as hell destination address checking
    # ------------------------------------------------
    script = []
    try:
        script = list(txout.scriptPubKey)
    except op.CScriptTruncatedPushDataError:
        pass

    # pay-to-pubkey-hash
    if (len(script) == 5 and
            script[0] == op.OP_DUP and
            script[1] == op.OP_HASH160 and
            script[3] == op.OP_EQUALVERIFY and
            script[4] == op.OP_CHECKSIG):
        script_type = 1
        dest_address = script[2]
    elif (len(script) == 3 and
          script[0] == op.OP_HASH160 and
          script[2] == op.OP_EQUAL):
        script_type = 0
        dest_address = script[1]
    elif len(script) == 2 and script[1] == op.OP_CHECKSIG:
        script_type = 2
        dest_address = serialize.Hash160(script[0])
    else:
        current_app.logger.warn("Unrecognized script {}"
                                .format(script))
    return dest_address, script_type