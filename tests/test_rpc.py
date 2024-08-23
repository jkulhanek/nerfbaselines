from functools import partial
import contextlib
from queue import Queue, Empty
import inspect
import threading
import random
import pytest
import time
from time import sleep
import gc
from typeguard import typeguard_ignore
from unittest import mock

from nerfbaselines.utils import CancelledException, CancellationToken
from nerfbaselines.backends._common import SimpleBackend


def _test_function(a, b):
    return a + b


def cc(*args):
    def w():
        o = None
        for fn in args:
            o = fn()
        return o
    return w


class _TestObject:
    static_property = 8

    def __init__(self, a):
        self.a = a

    @staticmethod
    def static_method(a, b):
        return a + b

    @property
    def attr(self):
        return self.a + 1

    def test_method(self, b, *, c):
        self.a += 2
        return self.a - b + c

    @property
    def attr2(self):
        return self.a + 2

    def test_iter(self):
        for i in range(5):
            yield i

    def test_iter_interrupt(self):
        for i in range(5):
            yield i
        raise RuntimeError("Not interrupted")


    def test_raise(self):
        raise ValueError("Test error")

    def test_cancel(self):
        from nerfbaselines.utils import CancellationToken

        for _ in range(1000):
            assert CancellationToken.current is not None
            CancellationToken.cancel_if_requested()
            sleep(1/1000)

    def test_cancel_iter(self):
        for i in range(1000):
            CancellationToken.cancel_if_requested()
            sleep(1/1000)
            yield i


@typeguard_ignore
def test_rpc_backend_static_function():
    from nerfbaselines.backends._rpc import RPCWorker, RPCBackend
    worker = RPCWorker()
    backend = RPCBackend(protocol=MockProtocol(worker))

    tf = _test_function
    test_mock = mock.MagicMock(side_effect=tf)
    with mock.patch(f"{tf.__module__}.{tf.__name__}", side_effect=test_mock):

        # Test simple call
        func = partial(backend.static_call, f"{tf.__module__}:{tf.__name__}")
        out = func(1, 2)
        test_mock.assert_called_once()
        assert out == 3
        test_mock.reset_mock()

        # Test simple call
        out = func(1, 3)
        test_mock.assert_called_once()
        assert out == 4
        test_mock.reset_mock()


@typeguard_ignore
def test_rpc_backend_instance():
    from nerfbaselines.backends._rpc import RPCWorker, RPCBackend
    worker = RPCWorker()
    worker.handle = mock.MagicMock(side_effect=worker.handle)
    backend = RPCBackend(protocol=MockProtocol(worker))

    out = backend.static_call(f"{_TestObject.__module__}:{_TestObject.__name__}", 1)
    assert len(worker._instances) == 1

    # Test simple call
    worker.handle.assert_called_once()
    assert out.test_method(5, c=3) == 1
    worker.handle.reset_mock()
    assert out.test_method(5, c=3) == 3
    worker.handle.reset_mock()

    # Test raise error
    with pytest.raises(ValueError) as e:
        out.test_raise()
        e.match("Test error")

    # Test instance was deleted
    del out
    gc.collect()
    assert not any(worker._instances)


class MockProtocol:
    def __init__(self, worker):
        self.worker = worker
        self._next_receive = None

    def send(self, message, interrupt=False):
        if interrupt:
            self.worker.handle_interrupt(message)
        else:
            self._next_receive = self.worker.handle(message),

    def receive(self):
        if self._next_receive is None:
            raise RuntimeError("No message to receive")
        out = self._next_receive[0]
        self._next_receive = None
        return out


def test_rpc_backend_yield():
    from nerfbaselines.backends._rpc import RPCWorker, RPCBackend
    worker = RPCWorker()
    backend = RPCBackend(protocol=MockProtocol(worker))
    inst = backend.static_call(f"{_TestObject.__module__}:{_TestObject.__name__}", 1)
    assert len(worker._instances) == 1

    # Mock backend.instance_del to measure how many times it was called
    worker._process_del = process_del = mock.MagicMock(side_effect=worker._process_del)

    # Test simple call
    assert list(inst.test_iter()) == list(range(5))

    assert len(worker._instances) == 1
    assert process_del.call_count == 0
    process_del.reset_mock()

    # Test interrupt iterator
    with pytest.raises(RuntimeError):
        list(inst.test_iter_interrupt())
    
    gc.collect()
    assert len(worker._instances) == 1
    assert process_del.call_count == 1
    process_del.reset_mock()

    for i in inst.test_iter_interrupt():
        if i > 2:
            break
    assert len(worker._instances) == 1
    assert process_del.call_count == 1
    process_del.reset_mock()

    class _iterator():
        def __iter__(self):
            self.i = -1
            return self

        def __next__(self):
            if self.i > 3:
                raise StopIteration
            self.i += 1
            return self.i

    with mock.patch.object(_TestObject, "test_iter", lambda self: _iterator()):
        # Test simple call
        out = []
        for i in inst.test_iter():
            assert len(worker._instances) == 2
            out.append(i)
        assert out == list(range(5))
        assert len(worker._instances) == 1

    # Test if passes isgenerator test
    import collections.abc
    iterator = inst.test_iter()
    assert isinstance(iterator, collections.abc.Iterator)

    del inst, iterator
    gc.collect()
    assert len(worker._instances) == 0


@typeguard_ignore
def test_rpc_backend_cancel():
    from nerfbaselines.backends._rpc import RPCWorker, RPCBackend
    worker = RPCWorker()
    backend = RPCBackend(protocol=MockProtocol(worker))
    assert len(worker._instances) == 0
    inst = backend.static_call(f"{_TestObject.__module__}:{_TestObject.__name__}", 1)
    assert len(worker._instances) == 1

    # Test cancel
    start = time.time()
    with CancellationToken() as token:
        inst.static_method(1, 2)

        assert len(worker._instances) == 1
        # Cleanup is done after the call
        assert len(worker._cancellation_tokens) == 0

    # Test cancel
    start = time.time()
    with CancellationToken() as token:
        thread = threading.Thread(target=lambda: sleep(0.001) or token.cancel(), daemon=True)
        thread.start()
        with pytest.raises(CancelledException):
            inst.test_cancel()
        thread.join()
        assert time.time() - start < 0.1
    assert len(worker._instances) == 1

    # Test cancel iterable
    start = time.perf_counter()
    with pytest.raises(CancelledException):
        with CancellationToken() as token:
            thread = threading.Thread(target=lambda: sleep(0.005) or token.cancel(), daemon=True)
            thread.start()
            list(inst.test_cancel_iter())
            thread.join()
    assert time.perf_counter() - start < 0.1

    del inst
    gc.collect()
    assert len(worker._instances) == 0

def test_simple_backend_static_function():
    backend = SimpleBackend()

    # Test simple call
    assert backend.static_call(f"{_test_function.__module__}:{_test_function.__name__}", 1, 2) == 3


def test_simple_backend_instance():
    backend = SimpleBackend()

    out = backend.static_call(f"{_TestObject.__module__}:{_TestObject.__name__}", 1)

    # Test simple call
    assert out.test_method(5, c=3) == 1
    assert out.test_method(5, c=3) == 3


def _test_function_exception():
    raise Exception("Test error b1")


def _test_function_base_exception():
    raise BaseException("Test error b2")


@pytest.mark.parametrize("protocol_classes", 
                         [["tcp-pickle"], ["shm-pickle"], ["tcp-pickle", "shm-pickle"], None])
@typeguard_ignore
def test_remote_process_rpc_backend(protocol_classes):
    from nerfbaselines.backends._rpc import AutoTransportProtocol, RemoteProcessRPCBackend

    # Test normal
    protocol = AutoTransportProtocol(protocol_classes=protocol_classes)
    class custom_err(Exception):
        pass
    with pytest.raises(custom_err):
        with RemoteProcessRPCBackend(protocol=protocol) as backend:
            # Test simple call
            assert backend.static_call(f"{_test_function.__module__}:{_test_function.__name__}", 1, 2) == 3

            # Create instance
            out = backend.static_call(f"{_TestObject.__module__}:{_TestObject.__name__}", 1)

            # Test simple call
            assert out.test_method(5, c=3) == 1
            assert out.test_method(5, c=3) == 3

            # Test raise error
            with pytest.raises(Exception) as e:
                backend.static_call(f"{_test_function_exception.__module__}:{_test_function_exception.__name__}")
                e.match("Test error b1")

            # Test raise base exception
            with pytest.raises(BaseException) as e:
                backend.static_call(f"{_test_function_base_exception.__module__}:{_test_function_base_exception.__name__}")
                e.match("Test error b2")

            # Test abrupt end doesn't cause deadlock
            raise custom_err()


def _test_function_cancel():
    for _ in range(1000):
        CancellationToken.cancel_if_requested()
        sleep(0.01)


@pytest.mark.parametrize("protocol_classes", 
                         [["tcp-pickle"], ["shm-pickle"], ["tcp-pickle", "shm-pickle"], None])
@typeguard_ignore
def test_remote_process_rpc_backend_cancel(protocol_classes):
    from nerfbaselines.backends._rpc import AutoTransportProtocol, RemoteProcessRPCBackend
    protocol = AutoTransportProtocol(protocol_classes=protocol_classes)
    with RemoteProcessRPCBackend(protocol=protocol) as endpoint:
        cancellation_token = CancellationToken()
        threading.Thread(target=lambda: time.sleep(1.5) or cancellation_token.cancel(), daemon=True).start()
        start = time.time()
        with pytest.raises(CancelledException):
            with cancellation_token:
                endpoint.static_call(_test_function_cancel.__module__+":"+_test_function_cancel.__name__)
    assert time.time() - start < 10.0
