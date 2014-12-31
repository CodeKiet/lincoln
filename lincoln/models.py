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
    total_in = db.Column(db.Numeric)
    total_out = db.Column(db.Numeric)
    # Difficulty of block when solved
    difficulty = db.Column(db.Float, nullable=False)
    # 3-8 letter code for the currency that was mined
    currency = db.Column(db.String, nullable=False)
    # The hashing algorith mused to solve the block
    algo = db.Column(db.String, nullable=False)

    __table_args__ = (
        db.Index('blockheight', 'height'),
    )

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
    total_in = db.Column(db.Numeric)
    total_out = db.Column(db.Numeric)

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
                               primary_key=True,
                               index=True)
    origin_tx = db.relationship('Transaction', foreign_keys=[origin_tx_hash],
                                backref='origin_txs')

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
    spent_tx = db.relationship('Transaction', foreign_keys=[spend_tx_id],
                               backref='spent_txs')

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