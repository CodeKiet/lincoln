from bitcoin.core import serialize
from flask import current_app
from lincoln import db
from lincoln.models import Address
import bitcoin.core.script as op


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


def get_addr(dest_address, addr_version):
    # lookup address object matching dest_addr
    addr = Address.query.filter_by(hash=dest_address,
                                   version=addr_version).first()

    if not addr:
        addr = Address(hash=dest_address,
                       version=addr_version,
                       currency=current_app.config['currency']['code'])
        db.session.add(addr)

    return addr