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

import os.path
import re
import shutil
from pathlib import Path
from typing import List

from qubesbuilder.component import QubesComponent
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import Executor, ExecutorError
from qubesbuilder.executors.container import ContainerExecutor
from qubesbuilder.log import get_logger
from qubesbuilder.plugins.build import BuildPlugin, BuildError

log = get_logger("build_rpm")


def provision_local_repository(
    build: str,
    repository_dir: Path,
    component: QubesComponent,
    dist: QubesDistribution,
    source_info: dict,
    packages_list: List,
    prep_artifacts_dir: Path,
    build_artifacts_dir: Path,
):
    """
    Provision local builder repository.
    """
    log.info(
        f"{component}:{dist}:{build}: Provisioning local repository '{repository_dir}'."
    )

    # Create target directory that will have hardlinks to SRPM and built RPMs
    target_dir = repository_dir / f"{component.name}_{component.version}"
    if target_dir.exists():
        shutil.rmtree(target_dir.as_posix())
    target_dir.mkdir(parents=True)

    try:
        # srpm
        srpm_path = prep_artifacts_dir / source_info["srpm"]
        target_path = target_dir / source_info["srpm"]
        # target_path.hardlink_to(srpm_path)
        os.link(srpm_path, target_path)

        # rpms
        for rpm in packages_list:
            rpm_path = build_artifacts_dir / "rpm" / rpm
            target_path = target_dir / rpm
            # target_path.hardlink_to(rpm_path)
            os.link(rpm_path, target_path)

        # buildinfo
        buildinfo_path = build_artifacts_dir / "rpm" / source_info["buildinfo"]
        target_path = target_dir / source_info["buildinfo"]
        os.link(buildinfo_path, target_path)
    except (ValueError, PermissionError, NotImplementedError, FileExistsError) as e:
        msg = f"{component}:{dist}:{build}: Failed to provision local repository."
        raise BuildError(msg) from e


class RPMBuildPlugin(BuildPlugin):
    """
    RPMBuildPlugin manages RPM distribution build.

    Stages:
        - build - Build RPMs and provision local repository.

    Entry points:
        - build
    """

    plugin_dependencies = ["source_rpm", "build"]

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        executor: Executor,
        plugins_dir: Path,
        artifacts_dir: Path,
        backend_vmm: str,
        verbose: bool = False,
        debug: bool = False,
        use_qubes_repo: dict = None,
    ):
        super().__init__(
            component=component,
            dist=dist,
            executor=executor,
            plugins_dir=plugins_dir,
            artifacts_dir=artifacts_dir,
            verbose=verbose,
            debug=debug,
            use_qubes_repo=use_qubes_repo,
            backend_vmm=backend_vmm,
        )

        # Add some environment variables needed to render mock root configuration
        self.environment.update(
            {
                "DIST": self.dist.name,
                "PACKAGE_SET": self.dist.package_set.replace("host", "dom0"),
            }
        )
        if self.use_qubes_repo:
            self.environment.update(
                {
                    "USE_QUBES_REPO_VERSION": str(
                        self.use_qubes_repo.get("version", None)
                    ),
                    "USE_QUBES_REPO_TESTING": "1"
                    if self.use_qubes_repo.get("testing", None)
                    else "0",
                }
            )

    def run(self, stage: str):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run(stage=stage)

        if stage != "build":
            return
        distfiles_dir = self.get_component_distfiles_dir()
        artifacts_dir = self.get_dist_component_artifacts_dir(stage)
        rpms_dir = artifacts_dir / "rpm"

        # Compare previous artifacts hash with current source hash
        if all(
            self.component.get_source_hash()
            == self.get_dist_artifacts_info(stage, build.mangle()).get(
                "source-hash", None
            )
            for build in self.parameters["build"]
        ):
            log.info(
                f"{self.component}:{self.dist}: Source hash is the same than already built source. Skipping."
            )
            return

        # Clean previous build artifacts
        if artifacts_dir.exists():
            shutil.rmtree(artifacts_dir.as_posix())
        artifacts_dir.mkdir(parents=True)

        # Create RPM folder
        rpms_dir.mkdir(parents=True)

        # Source artifacts
        prep_artifacts_dir = self.get_dist_component_artifacts_dir(stage="prep")

        # Local build repository
        repository_dir = self.get_repository_dir() / self.dist.distribution
        repository_dir.mkdir(parents=True, exist_ok=True)

        # Remove previous versions in order to keep the latest one only
        for build in repository_dir.glob(f"{self.component.name}_*"):
            shutil.rmtree(build.as_posix())

        for build in self.parameters["build"]:
            # spec file basename will be used as prefix for some artifacts
            build_bn = build.mangle()

            # Read information from source stage
            source_info = self.get_dist_artifacts_info(stage="prep", basename=build_bn)

            if not source_info.get("srpm", None):
                raise BuildError(
                    f"Cannot find SRPM for '{build}'. Missing 'prep' stage call?"
                )

            buildinfo_file = source_info["srpm"].replace(
                ".src.rpm", f".{self.dist.architecture}.buildinfo"
            )

            #
            # Build from SRPM
            #

            # Copy-in distfiles, content and source RPM
            copy_in = [
                (self.plugins_dir / "build_rpm", self.executor.get_plugins_dir()),
                (repository_dir, self.executor.get_repository_dir()),
                (
                    prep_artifacts_dir / source_info["srpm"],
                    self.executor.get_build_dir(),
                ),
            ] + [
                (self.plugins_dir / plugin, self.executor.get_plugins_dir())
                for plugin in self.plugin_dependencies
            ]

            copy_out = [
                (self.executor.get_build_dir() / "rpm" / rpm, rpms_dir)
                for rpm in source_info["rpms"]
            ]
            copy_out += [(self.executor.get_build_dir() / buildinfo_file, rpms_dir)]

            # Createrepo of local builder repository and ensure 'mock' group can access
            # build directory
            cmd = [
                f"cd {self.executor.get_repository_dir()}",
                "createrepo_c .",
                f"sudo chown -R user:mock {self.executor.get_build_dir()}",
            ]

            # Run 'mock' to build source RPM
            # On Fedora /usr/bin/mock is a (consolehelper) wrapper,
            # which among other things, strips environment variables"
            mock_conf = (
                f"{self.dist.fullname}-{self.dist.version}-{self.dist.architecture}.cfg"
            )
            mock_cmd = [
                "sudo --preserve-env=DIST,PACKAGE_SET,USE_QUBES_REPO_VERSION",
                "/usr/libexec/mock/mock --no-cleanup-after",
                f"--rebuild {self.executor.get_build_dir() / source_info['srpm']}",
                f"--root {self.executor.get_plugins_dir()}/source_rpm/mock/{mock_conf}",
                f"--resultdir={self.executor.get_build_dir()}",
            ]
            if isinstance(self.executor, ContainerExecutor):
                msg = f"{self.component}:{self.dist}:{build}: Mock isolation set to 'simple', build has full network access. Use 'qubes' executor for network-isolated build."
                log.warning(msg)
                mock_cmd.append("--isolation=simple")
            else:
                mock_cmd.append("--isolation=nspawn")
            if self.verbose:
                mock_cmd.append("--verbose")
            if self.use_qubes_repo and self.use_qubes_repo.get("version"):
                mock_cmd.append("--enablerepo=qubes-current")
            if self.use_qubes_repo and self.use_qubes_repo.get("testing"):
                mock_cmd.append("--enablerepo=qubes-current-testing")

            files_inside_executor_with_placeholders = [
                f"{self.executor.get_plugins_dir()}/source_rpm/mock/{mock_conf}"
            ]

            self.environment["BIND_MOUNT_ENABLE"] = "True"
            buildinfo_cmd = [
                "sudo --preserve-env=DIST,PACKAGE_SET,USE_QUBES_REPO_VERSION,BIND_MOUNT_ENABLE",
                "/usr/libexec/mock/mock",
                f"--root {self.executor.get_plugins_dir()}/source_rpm/mock/{mock_conf}",
                f'--chroot /plugins/build_rpm/scripts/rpmbuildinfo /builddir/build/SRPMS/{source_info["srpm"]} > {self.executor.get_build_dir()}/{buildinfo_file}',
            ]

            cmd += [" ".join(mock_cmd), " ".join(buildinfo_cmd)]

            # Move RPMs into a separate dir and generate packages list based on given
            # distribution tag. For example, 'fc32', 'fc32.qubes', etc.
            dist_tag_regex = re.compile(f".*\.({self.dist.tag}.*)\.src\.rpm")
            parsed_dist_tag = dist_tag_regex.match(source_info["srpm"])
            if parsed_dist_tag and parsed_dist_tag.group(1) != self.dist.tag:
                dist_tag = parsed_dist_tag.group(1)
            else:
                dist_tag = self.dist.tag
            cmd += [
                f"{self.executor.get_plugins_dir()}/build_rpm/scripts/filter-packages-by-dist-arch "
                f"{self.executor.get_build_dir()} {self.executor.get_build_dir()}/rpm {dist_tag} {self.dist.architecture}"
            ]
            try:
                self.executor.run(
                    cmd,
                    copy_in,
                    copy_out,
                    environment=self.environment,
                    no_fail_copy_out=True,
                    files_inside_executor_with_placeholders=files_inside_executor_with_placeholders,
                )
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{build}: Failed to build RPMs: {str(e)}."
                raise BuildError(msg) from e

            # Symlink SRPM into result RPMs
            srpm_path = prep_artifacts_dir / source_info["srpm"]
            os.link(srpm_path, rpms_dir / source_info["srpm"])

            # Get packages list that have been actually built from predicted ones
            packages_list = []
            for rpm in source_info["rpms"]:
                if os.path.exists(rpms_dir / rpm):
                    packages_list.append(rpm)

            info = {
                "srpm": source_info["srpm"],
                "rpms": packages_list,
                "buildinfo": buildinfo_file,
                "source-hash": self.component.get_source_hash(),
            }

            # Provision builder local repository
            provision_local_repository(
                build=build,
                component=self.component,
                dist=self.dist,
                repository_dir=repository_dir,
                source_info=info,
                packages_list=packages_list,
                prep_artifacts_dir=prep_artifacts_dir,
                build_artifacts_dir=artifacts_dir,
            )

            # Save package information we parsed for next stages
            self.save_dist_artifacts_info(stage=stage, basename=build_bn, info=info)
