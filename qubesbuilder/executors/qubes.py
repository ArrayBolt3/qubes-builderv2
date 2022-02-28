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
import shutil
import subprocess
import os

from pathlib import Path, PurePath
from typing import List, Tuple

from qubesbuilder.common import sanitize_line
from qubesbuilder.executors import Executor, ExecutorError, log


class QubesExecutor(Executor):
    def __init__(self, dispvm, **kwargs):
        # dispvm is the template used for creating a disposable qube.
        self.dispvm = dispvm
        self._kwargs = kwargs

    def copy_in(self, vm: str, source_path: Path, destination_dir: PurePath):
        src = source_path.expanduser().resolve()
        dst = destination_dir
        # FIXME: we can factor the commandlines.
        prepare_incoming_and_destination = [
            "/usr/bin/qvm-run-vm",
            vm,
            f"bash -c 'rm -rf /builder/incoming/{src.name} {dst.as_posix()}/{src.name}'",
        ]
        copy_to_incoming = [
            "/usr/lib/qubes/qrexec-client-vm",
            vm,
            "qubesbuilder.FileCopyIn",
            "/usr/lib/qubes/qfile-agent",
            str(src),
        ]
        move_to_destination = [
            "/usr/bin/qvm-run-vm",
            vm,
            f"bash -c 'mkdir -p {dst.as_posix()} && mv /builder/incoming/{src.name} {dst.as_posix()}'",
        ]
        try:
            log.debug(f"copy-in (cmd): {' '.join(prepare_incoming_and_destination)}")
            subprocess.run(prepare_incoming_and_destination, check=True)

            log.debug(f"copy-in (cmd): {' '.join(copy_to_incoming)}")
            subprocess.run(copy_to_incoming, check=True)

            log.debug(f"copy-in (cmd): {' '.join(move_to_destination)}")
            subprocess.run(move_to_destination, check=True)
        except subprocess.SubprocessError as e:
            raise ExecutorError from e

    def copy_out(self, vm, source_path: PurePath, destination_dir: Path):
        src = source_path
        dst = destination_dir.resolve()

        # Remove local file or directory if exists
        dst_path = dst / src.name
        if os.path.exists(dst_path):
            if dst_path.is_dir():
                shutil.rmtree(dst / src.name)
            else:
                os.remove(dst_path)

        dst.mkdir(parents=True, exist_ok=True)

        cmd = [
            "/usr/lib/qubes/qrexec-client-vm",
            vm,
            f"qubesbuilder.FileCopyOut+{str(src).replace('/', '__')}",
            "/usr/lib/qubes/qfile-unpacker",
            str(os.getuid()),
            str(dst),
        ]
        try:
            log.debug(f"copy-out (cmd): {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
        except subprocess.SubprocessError as e:
            raise ExecutorError from e

    def run(
        self,
        cmd: List[str],
        copy_in: List[Tuple[Path, PurePath]] = None,
        copy_out: List[Tuple[PurePath, Path]] = None,
        environment: dict = None,
        no_fail_copy_out=False,
    ):

        dispvm = None

        try:
            result = subprocess.check_output(
                ["qrexec-client-vm", "dom0", "admin.vm.CreateDisposable"],
                stdin=subprocess.DEVNULL,
            )
            dispvm = result.decode("utf8").replace("0\x00", "")

            # Start the DispVM by running creation of builder directory
            start_cmd = [
                "/usr/bin/qvm-run-vm",
                dispvm,
                f"bash -c 'sudo mkdir -p /builder && sudo chown -R user:user /builder'",
            ]
            subprocess.run(start_cmd, stdin=subprocess.DEVNULL)

            # copy-in hook
            for src_in, dst_in in copy_in or []:
                self.copy_in(dispvm, source_path=src_in, destination_dir=dst_in)

            bash_env = []
            if environment:
                for key, val in environment.items():
                    bash_env.append(f"{str(key)}='{str(val)}'")

            qvm_run_cmd = [
                "/usr/bin/qvm-run-vm",
                dispvm,
                f'env {" ".join(bash_env)} bash -c \'{" && ".join(cmd)}\'',
            ]

            log.info(f"{dispvm}: Executing '{' '.join(qvm_run_cmd)}'.")

            # stream output for command
            process = subprocess.Popen(
                qvm_run_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            while True:
                if not process.stdout:
                    log.error(f"{dispvm}: No output!")
                    break
                if process.poll() is not None:
                    break
                for line in process.stdout:
                    line = sanitize_line(line.rstrip(b"\n")).rstrip()
                    log.info(f"{dispvm}: output: {str(line)}")
            rc = process.poll()
            if rc != 0:
                raise ExecutorError(
                    f"{dispvm}: Failed to run '{' '.join(qvm_run_cmd)}' (status={rc})."
                )

            # copy-out hook
            for src_out, dst_out in copy_out or []:
                try:
                    self.copy_out(dispvm, source_path=src_out, destination_dir=dst_out)
                except ExecutorError as e:
                    # Ignore copy-out failure if requested
                    if no_fail_copy_out:
                        log.warning(f"File not found inside container: {src_out}.")
                        continue
                    raise e
        finally:
            # Shutdown the DispVM (automatically deleted)
            if dispvm:
                subprocess.run(
                    ["qrexec-client-vm", dispvm, "admin.vm.Shutdown"],
                    stdin=subprocess.DEVNULL,
                )
