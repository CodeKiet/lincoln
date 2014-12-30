import os
import sys
import jinja2
import subprocess
import yaml
import logging
import inspect

from flask import Flask, current_app
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.migrate import Migrate
from werkzeug.local import LocalProxy
from bitcoin.rpc import Proxy
from redis import Redis

import lincoln.filters as filters

root = os.path.abspath(os.path.dirname(__file__) + '/../')
db = SQLAlchemy()

coinserv = LocalProxy(
    lambda: getattr(current_app, 'rpc_connection', None))
redis_conn = LocalProxy(
    lambda: getattr(current_app, 'redis', None))


def create_app(log_level="INFO", config="/config.yml", global_config="/global.yml"):
    app = Flask(__name__, static_folder='../static', static_url_path='/static')
    app.secret_key = 'test'
    app.config.from_object(__name__)

    # inject all the yaml configs
    try:
        global_config_vars = yaml.load(open(root + global_config))
        app.config.update(global_config_vars)
    except FileNotFoundError:
        pass

    config_vars = yaml.load(open(root + config))
    app.config.update(config_vars)

    # set our template paths
    global_path = app.config.get('template_global_path', '../lincoln_global/templates')
    template_paths = [os.path.join(root, global_path), os.path.join(root, 'lincoln/templates')]
    template_loader = jinja2.ChoiceLoader([
            jinja2.FileSystemLoader(template_paths),
            app.jinja_loader])
    app.jinja_loader = template_loader

    # Init the db & migrations
    db.init_app(app)
    Migrate(app, db)

    # Setup redis
    redis_config = app.config.get('redis_conn', dict(type='live'))
    typ = redis_config.pop('type')
    if typ == "mock_redis":
        from mockredis import mock_redis_client
        app.redis = mock_redis_client()
    else:
        app.redis = Redis(**redis_config)

    del app.logger.handlers[0]
    app.logger.setLevel(logging.NOTSET)
    log_format = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s]: %(message)s')
    app.log_level = getattr(logging, str(log_level), app.config.get('log_level', "INFO"))

    logger = logging.getLogger()
    logger.setLevel(app.log_level)
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(log_format)
    logger.addHandler(handler)

    # try and fetch the git version information
    try:
        output = subprocess.check_output(b"git show -s --format='%ci %h'",
                                         shell=True).strip().rsplit(b" ", 1)
        app.config['hash'] = output[1]
        app.config['revdate'] = output[0]
    # celery won't work with this, so set some default
    except Exception:
        app.config['hash'] = ''
        app.config['revdate'] = ''

    # Dynamically add all the filters in the filters.py file
    for name, func in inspect.getmembers(filters, inspect.isfunction):
        app.jinja_env.filters[name] = func

    app.rpc_connection = Proxy(
        "http://{0}:{1}@{2}:{3}/"
        .format(app.config['coinserv']['username'],
                app.config['coinserv']['password'],
                app.config['coinserv']['address'],
                app.config['coinserv']['port']))

    from . import views
    app.register_blueprint(views.main)
    return app
