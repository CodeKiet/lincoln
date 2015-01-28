import logging
from decimal import Decimal
from collections import deque
import signal
import time
import datetime

import decorator
from flask import current_app
from flask.ext.script import Manager
from flask.ext.migrate import MigrateCommand
import sqlalchemy

from lincoln import create_app, db, coinserv
from lincoln.models import Block, Transaction, Output, Address
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


manager.add_option('-c', '--config', default='/config.yml')
manager.add_option('-l', '--log-level',
                   choices=['DEBUG', 'INFO', 'WARN', 'ERROR'], default='INFO')

if __name__ == "__main__":
    manager.run()
