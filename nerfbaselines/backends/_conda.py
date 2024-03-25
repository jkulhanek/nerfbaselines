import os
import subprocess
from pathlib import Path
import shlex
from typing import Optional
import hashlib

import nerfbaselines
from ..types import NB_PREFIX, TypedDict
from ..utils import get_package_dependencies
from ._rpc import RemoteProcessRPCBackend, get_safe_environment


_DEFAULT_TORCH_INSTALL_COMMAND = "pytorch==2.2.0 torchvision==0.17.0 pytorch-cuda=11.8 -c pytorch -c nvidia"


class CondaBackendSpec(TypedDict, total=False):
    environment_name: Optional[str]
    python_version: Optional[str]
    install_script: Optional[str]


def conda_get_environment_hash(spec: CondaBackendSpec):
    value = hashlib.sha256()

    def maybe_update(x):
        if x is not None:
            value.update(x.encode("utf8"))

    maybe_update(spec.get("python_version"))
    maybe_update(spec.get("install_script"))
    maybe_update(",".join(get_package_dependencies()))
    return value.hexdigest()


def conda_get_install_script(spec: CondaBackendSpec, package_path: Optional[str] = None, environment_path: Optional[str] = None):
    environment_name = spec.get("environment_name")
    assert environment_name is not None, "CondaBackend requires environment_name to be specified"
    environment_hash = conda_get_environment_hash(spec)

    custom_environment_path = False
    if environment_path is None:
        custom_environment_path = True
        environments_path = os.environ.get("NERFBASELINES_CONDA_ENVIRONMENTS", os.path.join(NB_PREFIX, "conda-envs"))
        environment_path = os.path.join(environments_path, environment_name, environment_hash, environment_name)
    env_path = environment_path
    args = []
    python_version = spec.get("python_version")
    if python_version is not None:
        args.append(f"python={python_version}")
    install_dependencies_script = ''
    dependencies = get_package_dependencies()
    if dependencies:
        install_dependencies_script = "pip install " + " ".join(f"'{x}'" for x in dependencies)
    if package_path is None:
        package_path = Path(nerfbaselines.__file__).absolute().parent.parent
    script = "set -eo pipefail\n"
    if not custom_environment_path:
        script += f"""
# Clear old environments
if [ -d {shlex.quote(os.path.dirname(env_path))} ]; then
    for hash in $(ls -1 {shlex.quote(os.path.dirname(os.path.dirname(env_path)))}); do
        if [ "$hash" != {shlex.quote(environment_hash)} ]; then
            rm -rf {shlex.quote(os.path.dirname(os.path.dirname(env_path)))}"/$hash"
        fi
    done
fi
"""
    script += f"""
# Create new environment
eval "$(conda shell.bash hook)"
if [ ! -e {shlex.quote(env_path + ".ack.txt")} ]; then
rm -rf {shlex.quote(env_path)}
conda deactivate
{shlex.join(["conda", "create", "--prefix", env_path, "-y", "-c", "conda-forge", "--override-channels"] + args)}
conda activate {shlex.quote(env_path)}
echo -e 'channels:\n  - conda-forge\n' > {shlex.quote(os.path.join(env_path, ".condarc"))}
conda install -y pip conda-build
mkdir -p {shlex.quote(os.path.join(env_path, "src"))}
cd {shlex.quote(os.path.join(env_path, "src"))}
{spec.get('install_script') or ''}
if ! python -c 'import torch' >/dev/null 2>&1; then conda install -y {shlex.join(_DEFAULT_TORCH_INSTALL_COMMAND.split())}; fi
if ! python -c 'import cv2' >/dev/null 2>&1; then pip install opencv-python-headless; fi
{install_dependencies_script}
if [ -e {shlex.quote(str(package_path))} ]; then
    conda develop {shlex.quote(str(package_path))}
fi
if [ ! nerfbaselines >/dev/null 2>&1 ]; then
    echo -e '#!/usr/bin/env python3\nif __name__ == "__main__":\nfrom nerfbaselines.__main__ import main\nmain()\n'>"$CONDA_PREFIX/bin/nerfbaselines"
    chmod +x "$CONDA_PREFIX/bin/nerfbaselines"
fi
echo '#!/bin/bash' > {shlex.quote(os.path.join(env_path, ".activate.sh"))}
echo 'eval "$(conda shell.bash hook)"' >> {shlex.quote(os.path.join(env_path, ".activate.sh"))}
echo 'conda activate {shlex.quote(env_path)};export NERFBASELINES_BACKEND=python' >> {shlex.quote(os.path.join(env_path, ".activate.sh"))}
echo 'exec "$@"' >> {shlex.quote(os.path.join(env_path, ".activate.sh"))}
chmod +x {shlex.quote(os.path.join(env_path, ".activate.sh"))}
touch {shlex.quote(env_path + ".ack.txt")}
echo "0" > {shlex.quote(env_path + ".ack.txt")}
fi
"""
    return script

class CondaBackend(RemoteProcessRPCBackend):
    name = "conda"

    def __init__(self, spec: CondaBackendSpec, address: str = "localhost", port: Optional[int] = None):
        self._spec = spec
        super().__init__(address=address, port=port, python_path="python")
        assert self._spec.get("environment_name") is not None, "CondaBackend requires environment_name to be specified"

    def _launch_worker(self, args, env):
        environments_path = os.environ.get("NERFBASELINES_CONDA_ENVIRONMENTS", os.path.join(NB_PREFIX, "conda-envs"))
        environment_name = self._spec.get("environment_name")
        assert environment_name is not None, "CondaBackend requires environment_name to be specified"
        env_path = os.path.join(environments_path, environment_name, conda_get_environment_hash(self._spec), environment_name)
        PACKAGE_PATH = Path(nerfbaselines.__file__).absolute().parent.parent
        env["PYTHONPATH"] = f'{PACKAGE_PATH}'
        args = [os.path.join(env_path, ".activate.sh")] + list(args)
        return super()._launch_worker(args, env)

    def install(self):
        subprocess.check_call(["bash", "-l", "-c", conda_get_install_script(self._spec)])

    def shell(self):
        environments_path = os.environ.get("NERFBASELINES_CONDA_ENVIRONMENTS", os.path.join(NB_PREFIX, "conda-envs"))
        environment_name = self._spec.get("environment_name")
        assert environment_name is not None, "CondaBackend requires environment_name to be specified"
        env_path = os.path.join(environments_path, environment_name, conda_get_environment_hash(self._spec), environment_name)
        env = get_safe_environment()
        env["PYTHONPATH"] = Path(nerfbaselines.__file__).absolute().parent.parent
        args = [os.path.join(env_path, ".activate.sh")] + ["bash"]
        os.execvpe(args[0], args, env)
