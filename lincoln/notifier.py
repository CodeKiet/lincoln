import os
from pyinotify import ProcessEvent
import yaml


class NotifyCallback(ProcessEvent):

    def __init__(self, app, g_cfg_location):
        super(NotifyCallback, self).__init__()
        self.g_cfg_location = g_cfg_location
        self.app = app

    def process_IN_MODIFY(self, event):
        self.app.logger.info("Modify: %s" % os.path.join(event.path, event.name))
        global_config_vars = yaml.load(open(self.g_cfg_location))
        self.app.config.update(global_config_vars)