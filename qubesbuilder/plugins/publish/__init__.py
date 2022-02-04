# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

from qubesbuilder.component import Component
from qubesbuilder.dist import Dist
from qubesbuilder.executors import Executor
from qubesbuilder.executors.local import LocalExecutor
from qubesbuilder.log import get_logger
from qubesbuilder.plugins import Plugin, PluginException

log = get_logger("publish")


class PublishException(PluginException):
    pass


class PublishPlugin(Plugin):
    """
    PublishPlugin manages generic distribution publication.
    """

    def __init__(self, component: Component, dist: Dist, executor: Executor, plugins_dir: Path,
                 artifacts_dir: Path, qubes_release: str, gpg_client: str, sign_key: dict,
                 publish_repository: str, verbose: bool = False, debug: bool = False):
        super().__init__(component=component, dist=dist, plugins_dir=plugins_dir,
                         artifacts_dir=artifacts_dir, verbose=verbose, debug=debug)

        self.executor = executor
        self.verbose = verbose
        self.debug = debug
        self.qubes_release = qubes_release
        self.publish_repository = publish_repository
        self.gpg_client = gpg_client
        self.sign_key = sign_key

    def run(self, stage: str):
        if stage == "publish" and not isinstance(self.executor, LocalExecutor):
            raise PublishException("This plugin only supports local executor.")
