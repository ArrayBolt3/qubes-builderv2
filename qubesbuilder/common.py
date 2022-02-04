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
from string import digits, ascii_letters

PROJECT_PATH = Path(__file__).resolve().parents[1]


def is_filename_valid(filename: str) -> bool:
    if filename.startswith("-") or filename == "":
        return False
    for c in filename:
        if c not in digits + ascii_letters + '-_.+':
            return False
    return True


# Originally from QubesOS/qubes-builder/rpc-services/qubesbuilder.BuildLog
def sanitize_line(untrusted_line):
    line = bytearray(untrusted_line)
    for i, c in enumerate(line):
        if 0x20 <= c <= 0x7e:
            pass
        else:
            line[i] = 0x2e
    return bytearray(line).decode('ascii')
