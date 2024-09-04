import functools
import sys
import re
from collections import deque
import threading
import importlib
from pathlib import Path
import subprocess
from typing import Optional
from typing import  Union, Set, Callable, List, cast, Dict, Any, Tuple
from typing import Sequence
from nerfbaselines import BackendName, MethodSpec
try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal


_mounted_paths = {}
_forwarded_ports = {}
_active_backend = {}


def mount(ps: Union[str, Path], pd: Union[str, Path]):
    tid = threading.get_ident()
    if _active_backend.get(tid):
        raise RuntimeError("Cannot mount while backend is active")
    if tid not in _mounted_paths:
        _mounted_paths[tid] = {}
    dest = str(Path(pd).absolute())
    _mounted_paths[tid][dest] = str(Path(ps).absolute())
    class _Mount:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            del args
            if tid in _mounted_paths and dest in _mounted_paths[tid]:
                _mounted_paths[tid].pop(dest)
            if tid in _mounted_paths and not _mounted_paths[tid]:
                del _mounted_paths[tid]
    return _Mount()


def get_mounts():
    tid = threading.get_ident()
    out = []
    for dest, src in _mounted_paths.get(tid, {}).items():
        out.append((src, dest))
    return out


def forward_port(ps: int, pd: int):
    tid = threading.get_ident()
    if _active_backend.get(tid):
        raise RuntimeError("Cannot forward ports while backend is active")
    if tid not in _forwarded_ports:
        _forwarded_ports[tid] = {}
    _forwarded_ports[tid][pd] = ps
    class _Forward:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            del args
            if tid in _forwarded_ports and pd in _forwarded_ports[tid]:
                _forwarded_ports[tid].pop(pd)
            if tid in _forwarded_ports and not _forwarded_ports[tid]:
                del _forwarded_ports[tid]
    return _Forward()


def get_forwarded_ports():
    tid = threading.get_ident()
    out = []
    for dest, src in _forwarded_ports.get(tid, {}).items():
        out.append((src, dest))
    return out


def get_implemented_backends(method_spec: 'MethodSpec') -> Sequence[BackendName]:
    from ._apptainer import get_apptainer_spec
    from ._docker import get_docker_spec

    backends: Set[BackendName] = set(("python",))
    if method_spec.get("conda") is not None:
        backends.add("conda")

    if get_docker_spec(method_spec) is not None:
        backends.add("docker")

    if get_apptainer_spec(method_spec) is not None:
        backends.add("apptainer")

    backends_order: List[BackendName] = ["conda", "docker", "apptainer", "python"]
    bo = method_spec.get("backends_order")
    if bo is not None:
        backends_order = list(bo) + [x for x in backends_order if x not in bo]
    return [x for x in backends_order if x in backends]


def _get_default_backend(implemented_backends: Sequence[BackendName]) -> BackendName:
    should_install = []
    for backend in implemented_backends:
        if backend not in implemented_backends:
            continue
        if backend == "python":
            return "python"
        try:
            if backend == "conda":
                test_args = ["conda", "--version"]
            elif backend == "docker":
                test_args = ["docker", "-v"]
            elif backend == "apptainer":
                test_args = ["apptainer", "-v"]
            else:
                raise ValueError(f"Unknown backend {backend}")
            ret = subprocess.run(test_args, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ret.returncode == 0:
                return backend
        except FileNotFoundError:
            pass
            should_install.append(backend)
    raise RuntimeError("No backend available, please install " + " or ".join(should_install))


def get_backend(method_spec: "MethodSpec", backend: Optional[str]) -> 'Backend':
    implemented_backends = get_implemented_backends(method_spec)
    if backend is None:
        backend = _get_default_backend(implemented_backends)
    elif backend not in implemented_backends:
        raise RuntimeError(f"Backend {backend} is not implemented for selected method.\nImplemented backends: {','.join(implemented_backends)}")

    if backend == "python":
        return SimpleBackend()
    elif backend == "conda":
        from ._conda import CondaBackend
        spec = method_spec.get("conda")
        assert spec is not None, "conda_spec is not defined"
        return CondaBackend(spec)
    elif backend == "docker":
        from ._docker import DockerBackend, get_docker_spec
        spec = get_docker_spec(method_spec)
        assert spec is not None, "docker_spec is not defined"
        return DockerBackend(spec)
    elif backend == "apptainer":
        from ._apptainer import ApptainerBackend, get_apptainer_spec
        spec = get_apptainer_spec(method_spec)
        assert spec is not None, "apptainer_spec is not defined"
        return ApptainerBackend(spec)
    else:
        raise ValueError(f"Unknown backend {backend}")


class _BackendMeta(type):
    @property
    def current(cls) -> Optional['Backend']:
        tid = threading.get_ident()
        if tid in _active_backend and _active_backend[tid]:
            return _active_backend[tid][-1]
        return None


class Backend(metaclass=_BackendMeta):
    name = "unknown"

    def __enter__(self):
        tid = threading.get_ident()
        if tid not in _active_backend:
            _active_backend[tid] = deque()
        _active_backend[tid].append(self)
        return self

    def __exit__(self, *args):
        del args
        tid = threading.get_ident()
        if tid in _active_backend and _active_backend[tid]:
            _active_backend[tid].pop()
        if not _active_backend[tid]:
            del _active_backend[tid]

    def install(self):
        pass

    def shell(self, args: Optional[Tuple[str, ...]] = None):
        del args
        raise NotImplementedError("shell not implemented")

    def static_call(self, function: str, *args, **kwargs):
        del function, args, kwargs
        raise NotImplementedError("static_call not implemented")

    def instance_call(self, instance: int, method: str, *args, **kwargs):
        del instance, method, args, kwargs
        raise NotImplementedError("instance_call not implemented")

    def instance_del(self, instance: int):
        del instance
        raise NotImplementedError("instance_del not implemented")


class SimpleBackend(Backend):
    name = "python"

    def __init__(self):
        self._instances = {}

    def static_call(self, function: str, *args, **kwargs):
        fn, fnname = function.split(":", 1)
        fn = importlib.import_module(fn)
        for part in fnname.split("."):
            fn = getattr(fn, part)
        fn = cast(Callable, getattr(fn, "__run_on_host_original__", fn))
        return fn(*args, **kwargs)

    def instance_call(self, instance: int, method: str, *args, **kwargs):
        instance = self._instances[instance]
        fn = getattr(instance, method)
        return fn(*args, **kwargs)

    def instance_del(self, instance: int):
        del self._instances[instance]


def get_package_dependencies(extra=None, ignore: Optional[Set[str]] = None, ignore_viewer: bool = False):
    assert __package__ is not None, "Package must be set"
    if sys.version_info < (3, 10):
        from importlib_metadata import distribution
        import importlib_metadata
    else:
        from importlib import metadata as importlib_metadata
        from importlib.metadata import distribution

    requires = set()
    requires_with_conditions = None
    try:
        requires_with_conditions = distribution(__package__).requires
    except importlib_metadata.PackageNotFoundError:
        # Package not installed
        pass
    for r in (requires_with_conditions or ()):
        if ";" in r:
            r, condition = r.split(";")
            r = r.strip().replace(" ", "")
            condition = condition.strip().replace(" ", "")
            if condition.startswith("extra=="):
                extracond = condition.split("==")[1][1:-1]
                if extra is not None and extracond in extra:
                    requires.add(r)
                continue
            elif condition.startswith("python_version"):
                requires.add(r)
                continue
            else:
                raise ValueError(f"Unknown condition {condition}")
        r = r.strip().replace(" ", "")
        requires.add(r)
    if ignore_viewer:
        # NOTE: Viewer is included in the package by default
        # See https://github.com/pypa/setuptools/pull/1503
        ignore = set(ignore or ())
        ignore.add("viser")

    if ignore is not None:
        ignore = set(x.lower() for x in ignore)
        for r in list(requires):
            rsimple = re.sub(r"[^a-zA-Z0-9_-].*", "", r).lower()
            if rsimple in ignore:
                requires.remove(r)
    return sorted(requires)


def run_on_host():
    def wrap(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            if Backend.current is not None:
                return Backend.current.static_call(f"{fn.__module__}:{fn.__name__}", *args, **kwargs)
            return fn(*args, **kwargs)
        wrapped.__run_on_host_original__ = fn  # type: ignore
        return wrapped
    return wrap


def setup_logging(verbose: Union[bool, Literal['disabled']]):
    import logging

    class Formatter(logging.Formatter):
        def format(self, record: logging.LogRecord):
            levelname = record.levelname[0]
            message = record.getMessage()
            if levelname == "D":
                return f"\033[0;36mdebug:\033[0m {message}"
            elif levelname == "I":
                return f"\033[1;36minfo:\033[0m {message}"
            elif levelname == "W":
                return f"\033[0;1;33mwarning: {message}\033[0m"
            elif levelname == "E":
                return f"\033[0;1;31merror: {message}\033[0m"
            else:
                return message

    kwargs: Dict[str, Any] = {}
    if sys.version_info >= (3, 8):
        kwargs["force"] = True
    if verbose == "disabled":
        logging.basicConfig(level=logging.FATAL, **kwargs)
        logging.getLogger('PIL').setLevel(logging.FATAL)
        try:
            import tqdm as _tqdm
            old_init = _tqdm.tqdm.__init__
            _tqdm.tqdm.__init__ = lambda *args, disable=None, **kwargs: old_init(*args, disable=True, **kwargs)
        except ImportError:
            pass
    elif verbose:
        logging.basicConfig(level=logging.DEBUG, **kwargs)
        logging.getLogger('PIL').setLevel(logging.WARNING)
    else:
        import warnings
        logging.basicConfig(level=logging.INFO, **kwargs)
        warnings.formatwarning = lambda message, *args, **kwargs: message
    for handler in logging.root.handlers:
        handler.setFormatter(Formatter())
    logging.captureWarnings(True)
