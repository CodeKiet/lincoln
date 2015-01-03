import os
import bitcoin.core as core

from flask import render_template, Blueprint, send_from_directory, current_app, \
    g, request

from . import models as m
from . import root

main = Blueprint('main', __name__)


@main.before_request
def glob_vars():
    g.currency = current_app.config['currency']['name']
    g.assets_address = current_app.config['assets_address']
    g.rev_hash = current_app.config['hash']
    if 'currencies' in current_app.config:
        g.currencies = current_app.config['currencies']


@main.route('/address/<address>')
def address(address):
    outputs_per_page = int(current_app.config.get('outputs_per_page', 15))

    index = int(request.args.get('index', 0))
    if index < 0:
        index = 0
    offset = index * outputs_per_page

    similar_addrs = m.Address.get_search_results(address)
    if len(similar_addrs) == 1:
        outputs = similar_addrs[0].outputs[offset:offset + outputs_per_page]
        return render_template('address.html',
                               address_obj=similar_addrs[0],
                               outputs=outputs,
                               outputs_per_page=outputs_per_page,
                               index=index)

    return render_template('search_results.html',
                           addresses=similar_addrs)


@main.route('/block/<hash>')
def block(hash):
    block = m.Block.query.filter_by(hash=core.lx(hash)).first()
    return render_template('block.html', block=block)


@main.route('/transaction/<hash>')
def transaction(hash):
    transaction = m.Transaction.query.filter_by(txid=core.lx(hash)).first()
    return render_template('transaction.html', transaction=transaction)


@main.route("/transactions")
def transactions():
    trans_per_page = int(current_app.config.get('trans_per_page', 25))

    index = int(request.args.get('index', 0))
    if index < 0:
        index = 0
    offset = index * trans_per_page
    transactions = (m.Transaction.query
                                 .order_by(m.Transaction.id.desc())
                                 .offset(offset)
                                 .limit(trans_per_page))

    return render_template('transactions.html',
                           transactions=transactions,
                           index=index)


@main.route('/')
@main.route("/blocks")
def blocks():
    trans_per_page = int(current_app.config.get('blocks_per_page', 20))

    index = int(request.args.get('index', 0))
    if index < 0:
        index = 0
    offset = index * trans_per_page
    blocks = (m.Block.query.order_by(m.Block.height.desc())
                           .offset(offset)
                           .limit(trans_per_page))

    return render_template('blocks.html',
                           blocks=blocks,
                           currency=current_app.config['currency']['name'],
                           index=index)


@main.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(root, 'static'),
        'favicon.ico', mimetype='image/vnd.microsoft.icon')


@main.route('/search/<query>')
def search(query):
    outputs_per_page = int(current_app.config.get('outputs_per_page', 15))

    index = int(request.args.get('index', 0))
    if index < 0:
        index = 0
    offset = index * outputs_per_page

    # Get matching addresses
    addresses = m.Address.get_search_results(query)
    if len(addresses) == 1:
        outputs = addresses[0].outputs[offset:offset + outputs_per_page]
        return render_template('address.html',
                               address_obj=addresses[0],
                               outputs=outputs,
                               outputs_per_page=outputs_per_page,
                               index=index)

    # Get matching transactions
    transactions = m.Transaction.get_search_results(query)
    if len(transactions) == 1:
        return render_template('transaction.html', transaction=transactions[0])

    # Get matching blocks
    blocks = m.Block.get_search_results(query)
    if len(blocks) == 1:
        return render_template('block.html', block=blocks[0])

    return render_template('search_results.html',
                           blocks=blocks,
                           transactions=transactions,
                           addresses=addresses)
