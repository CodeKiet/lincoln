import calendar
import binascii
import bitcoin.core as core
import bitcoin.base58 as base58
from flask import current_app
import sqlalchemy
from lincoln.utils import get_int_from_str

from .model_lib import base
from . import db


class Block(base):
    """ This class stores metadata on all blocks found by the pool """
    # An id value to make foreign keys more compact
    id = db.Column(db.Integer, primary_key=True)
    # the hash of the block
    hash = db.Column(db.LargeBinary(64), unique=True)
    height = db.Column(db.Integer, nullable=False)
    # The actual internal timestamp on the block
    ntime = db.Column(db.DateTime, nullable=False)
    # Is block now orphaned?
    orphan = db.Column(db.Boolean, default=False)
    # Cache of all transactions in and out
    total_in = db.Column(db.Numeric, default=0)
    total_out = db.Column(db.Numeric, default=0)
    # Difficulty of block when solved
    difficulty = db.Column(db.Float, nullable=False)
    # 3-8 letter code for the currency that was mined
    currency = db.Column(db.String, nullable=False)
    # The hashing algorith mused to solve the block
    algo = db.Column(db.String, nullable=False)

    __table_args__ = (
        db.Index('blockheight', 'height'),
    )

    def recalculate_total_in(self):
        self.total_in = sum([tx.total_in for tx in self.transactions])
        return self.total_in

    def recalculate_total_out(self):
        self.total_out = sum([tx.total_out for tx in self.transactions])
        return self.total_out

    @property
    def timestamp(self):
        return calendar.timegm(self.ntime.utctimetuple())

    @property
    def hash_str(self):
        return core.b2lx(self.hash)

    @property
    def url_for(self):
        return "/block/{}".format(self.hash_str)

    @property
    def coinbase_value(self):
        return self.total_out - self.total_in

    def __str__(self):
        return "<{} h:{} hsh:{}>".format(self.currency, self.height, self.hash_str)

    @classmethod
    def format_query_str(cls, query_str):
        """
        Takes a string, convert it to an object which can be used to query
        the Address class & returns it. Otherwise it returns False.
        """
        try:
            hash = core.lx(query_str)
        except binascii.Error:
            return False
        else:
            return hash

    @classmethod
    def get_search_results(cls, query_str):
        """
        Takes an address pkh, queries for addresses
        """
        # Check if the str is a valid blockheight
        blockheight = get_int_from_str(query_str)
        if blockheight:
            try:
                block = cls.query.filter_by(height=blockheight).one()
            except sqlalchemy.exc.SQLAlchemyError:
                pass
            else:
                return [block]

        # Not a blockheight, try to match it to a block hash
        bhash = cls.format_query_str(query_str)
        if not bhash:
            return []

        limit = current_app.config.get('search_result_limit', 10)
        try:
            blocks = cls.query.filter(cls.hash.like(bhash)).limit(limit).all()
        except sqlalchemy.exc.SQLAlchemyError:
            return []
        else:
            return blocks

    def remove(self):
        current_app.logger.debug("Preparing to remove Block {}".format(self))
        # Delete transactions
        for tx in self.transactions:
            tx.remove()
        db.session.delete(self)
        db.session.flush()


class Transaction(base):
    id = db.Column(db.Integer, primary_key=True)
    txid = db.Column(db.LargeBinary(64), unique=True)
    network_fee = db.Column(db.Numeric)
    coinbase = db.Column(db.Boolean, default=False)
    # Points to the main chain block that it's in, or null if in mempool
    block_id = db.Column(db.Integer, db.ForeignKey('block.id'))
    block = db.relationship('Block', foreign_keys=[block_id],
                            backref='transactions')
    # Cache of all outputs in and out
    total_in = db.Column(db.Numeric, default=0)
    total_out = db.Column(db.Numeric, default=0)

    def recalculate_total_in(self):
        self.total_in = sum([input.amount for input in self.inputs])
        return self.total_in

    def recalculate_total_out(self):
        self.total_out = sum([output.amount for output in self.outputs])
        return self.total_out

    @property
    def hash_str(self):
        return core.b2lx(self.txid)

    @property
    def url_for(self):
        return "/transaction/{}".format(self.hash_str)

    def __str__(self):
        return "<Transaction h:{}>".format(self.hash_str)

    @classmethod
    def format_query_str(cls, query_str):
        """
        Takes a string, convert it to an object which can be used to query
        the Transaction class & returns it. Otherwise it returns False.
        """
        try:
            hash = core.lx(query_str)
        except binascii.Error:
            return False
        else:
            return hash

    @classmethod
    def get_search_results(cls, query_str):
        """
        Takes an address pkh, queries for addresses
        """
        hash = cls.format_query_str(query_str)
        if not hash:
            return []

        limit = current_app.config.get('search_result_limit', 10)
        try:
            txs = cls.query.filter(cls.txid.like(hash)).limit(limit).all()
        except sqlalchemy.exc.SQLAlchemyError:
            return []
        else:
            return txs

    @classmethod
    def get(cls, tx_hash, block_obj):
        # We don't want to have to do a rollback, so query for the TX first
        try:
            tx_obj = cls.query.filter_by(txid=tx_hash).one()
            tx_obj.block = block_obj
            tx_obj.total_out = 0
            tx_obj.total_in = 0
            current_app.logger.debug("Found old tx & overwrote {}".format(tx_obj))
        except sqlalchemy.orm.exc.NoResultFound:
            tx_obj = cls(block=block_obj, txid=tx_hash)
            db.session.add(tx_obj)
            current_app.logger.debug("Found new tx {}".format(tx_obj))
        db.session.flush()
        return tx_obj

    def remove(self):
        current_app.logger.debug("Preparing to remove TX {}".format(self))
        # Update block balances
        self.block.total_out -= self.total_out
        self.block.total_in -= self.total_in
        # Delete tx inputs
        for input in self.inputs:
            input.remove()
        # Delete tx outputs
        for output in self.outputs:
            output.remove()
        self.block.transactions.remove(self)
        db.session.delete(self)
        db.session.flush()


class Address(base):
    # An id value to make foreign keys more compact
    id = db.Column(db.Integer, primary_key=True)
    # the hash of the address
    hash = db.Column(db.LargeBinary, unique=True, nullable=False, index=True)

    version = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String, nullable=False)

    # Cached metadata
    total_in = db.Column(db.Numeric, default=0)
    total_out = db.Column(db.Numeric, default=0)
    first_seen_at = db.Column(db.DateTime)

    __table_args__ = (
        db.Index('address_version', 'version'),
    )

    def recalculate_total_in(self):
        self.total_in = sum([output.amount for output in self.outputs])
        return self.total_in

    def recalculate_total_out(self):
        self.total_out = sum([output.amount for output in self.outputs
                              if output.spend_tx_id])
        return self.total_out

    @property
    def hash_str(self):
        return str(base58.CBase58Data.from_bytes(
            self.hash,
            nVersion=self.version))

    @property
    def url_for(self):
        return "/address/{}".format(self.hash_str)

    @property
    def balance(self):
        return self.total_in - self.total_out

    def __str__(self):
        return "<Address h:{}>".format(self.hash_str)

    @classmethod
    def get_addr(cls, address, addr_version):
        # lookup address object matching dest_addr
        addr = Address.query.filter_by(hash=address, version=addr_version).first()
        if addr:
            return addr

        addr = Address(hash=address,
                       version=addr_version,
                       currency=current_app.config['currency']['code'])
        db.session.add(addr)
        db.session.flush()
        return addr

    @classmethod
    def format_query_str(cls, query_str):
        """
        Takes a string, convert it to an object which can be used to query
        the Address class & returns it. Otherwise it returns False.
        """
        try:
            pubkey_hash = base58.decode(query_str)
        except base58.InvalidBase58Error:
            return False
        else:
            # Strip off the version and checksum, database doesn't store them
            return pubkey_hash[1:-4]

    @classmethod
    def get_search_results(cls, query_str):
        """
        Takes an address pkh, queries for addresses
        """
        pkhash = cls.format_query_str(query_str)
        if not pkhash:
            return []

        limit = current_app.config.get('search_result_limit', 10)
        try:
            addresses = cls.query.filter(cls.hash.like(b"%" + pkhash + b"%")).limit(limit).all()
        except sqlalchemy.exc.SQLAlchemyError:
            return []
        else:
            return addresses


class Output(base):
    type_map_str = {0: "p2sh", 1: "p2pkh", 2: "p2pk", 3: "non-std"}
    type_map_color = {0: "warning", 1: "danger", 2: "info", 3: "default"}
    type_map_icon = {0: "&#xf084;", 1: "&#xf084;", 2: "&#xf0a3;", 3: "&#xf068;"}
    type = db.Column(db.SmallInteger)

    # Where this Output was created at
    origin_tx_hash = db.Column(db.LargeBinary(64),
                               db.ForeignKey('transaction.txid'),
                               primary_key=True, index=True)
    origin_tx = db.relationship('Transaction', foreign_keys=[origin_tx_hash],
                                backref='outputs')

    # The amount it's worth
    amount = db.Column(db.Numeric)
    # It's index in the previous tx. Used to query when trying to spend it
    index = db.Column(db.SmallInteger, primary_key=True)

    # Address that gets to spend this output. Will be null for unusual tx types
    address_hash = db.Column(db.LargeBinary, db.ForeignKey('address.hash'),
                             index=True)
    address = db.relationship('Address', foreign_keys=[address_hash],
                              backref='outputs')

    # Point to the tx we spent this output in, or null if UTXO
    spend_tx_id = db.Column(db.Integer, db.ForeignKey('transaction.id'),
                            index=True)
    spend_tx = db.relationship('Transaction', foreign_keys=[spend_tx_id],
                               backref='inputs')

    @property
    def type_icon(self):
        return self.type_map_icon[self.type]

    @property
    def type_color(self):
        return self.type_map_color[self.type]

    @property
    def type_str(self):
        return self.type_map_str[self.type]

    @property
    def dest_address(self):
        return self.address_hash

    @property
    def address_str(self):
        return self.address.hash_str

    @property
    def url_for(self):
        return "/transaction/{}".format(self.txid)

    @property
    def timestamp(self):
        return calendar.timegm(self.created_at.utctimetuple())

    @classmethod
    def get_input(cls, tx_hash, i):
        out = cls.query.filter_by(origin_tx_hash=tx_hash, index=i).one()
        # TODO: Re-lookup if output not located
        # TODO: Add catch for multiple outputs found
        db.session.flush()
        return out

    @classmethod
    def get_output(cls, tx_hash, amount, i):
        try:
            out = cls.query.filter_by(origin_tx_hash=tx_hash, amount=amount,
                                      index=i).one()
        except sqlalchemy.orm.exc.NoResultFound:
            try:
                out = cls.query.filter_by(origin_tx_hash=tx_hash,
                                          amount=amount).one()
                out.index = i
            # TODO: Add catch for multiple outputs found
            except sqlalchemy.orm.exc.NoResultFound:
                out = cls(origin_tx_hash=tx_hash, index=i, amount=amount)
                db.session.add(out)
        db.session.flush()
        return out

    def remove(self):
        current_app.logger.debug("Preparing to remove Output {}".format(self))
        # Update address balances
        if self.address:
            self.address.total_in -= self.amount
            if self.spend_tx_id:
                self.address.total_out -= self.amount
        # Update transaction balances
        if self.origin_tx_hash:
            self.origin_tx.total_out -= self.amount
            self.origin_tx.outputs.remove(self)
        if self.spend_tx_id:
            self.spend_tx.total_in -= self.amount
            self.spend_tx.inputs.remove(self)
        db.session.delete(self)
        db.session.flush()