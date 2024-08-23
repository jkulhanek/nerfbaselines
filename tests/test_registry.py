from unittest import mock
from nerfbaselines.types import Method

class _TestMethod(Method):
    _test = 1

    def __init__(self):
        self.test = self._test

    def optimize_embeddings(self, *args, **kwargs):
        raise NotImplementedError()

    @classmethod
    def get_method_info(cls):
        raise NotImplementedError()

    def get_info(self):
        raise NotImplementedError()

    def render(self, *args, **kwargs):
        raise NotImplementedError()

    def train_iteration(self, *args, **kwargs):
        raise NotImplementedError()

    def save(self, *args, **kwargs):
        raise NotImplementedError()

    def get_train_embedding(self, *args, **kwargs):
        raise NotImplementedError()



def test_registry_build_method():
    from nerfbaselines.registry import build_method, MethodSpec, methods_registry as registry, get_method_spec

    spec_dict: MethodSpec = {
        "id": "test",
        "method": _TestMethod.__module__ + ":_TestMethod",
        "conda": {
            "environment_name": "test",
            "install_script": "",
        },
    }
    with mock.patch.dict(registry, test=spec_dict):
        spec = get_method_spec("test")
        with build_method(spec, backend="python") as method_cls:
            method = method_cls()
            assert isinstance(method, _TestMethod)
    
    spec_dict: MethodSpec = {
        "id": "test",
        "method": _TestMethod.__module__ + ":_TestMethod",
        "conda": {
            "environment_name": "test",
            "install_script": "",
        },
    }

    with mock.patch.dict(registry, test=spec_dict):
        spec = get_method_spec("test")
        with build_method(spec, backend="python") as method_cls:
            method = method_cls()
            assert isinstance(method, _TestMethod)


def test_register_spec():
    from nerfbaselines.registry import register, build_method
    from nerfbaselines import registry

    with mock.patch.object(registry, "methods_registry", {}):
        register({
            "method": test_register_spec.__module__ + ":_TestMethod",
            "conda": {
                "environment_name": "test",
                "install_script": "",
            },
            "id": ("_test_" + test_register_spec.__name__),
        })
        method_spec = registry.get_method_spec("_test_" + test_register_spec.__name__)
        with build_method(method_spec, backend="python") as method_cls:
            method = method_cls()
            assert isinstance(method, _TestMethod)
            assert method.test == 1


def test_get_presets_to_apply():
    from nerfbaselines import registry

    spec: registry.MethodSpec = {
        "method": test_register_spec.__module__ + ":_TestMethod",
        "conda": {
            "environment_name": "test",
            "install_script": "",
        },
        "id": "test",
        "presets": {
            "p1": { "@apply": [{ "dataset": "test-dataset" }] },
            "p2": {},
            "p3": { "@apply": [{ "dataset": "test-dataset", "scene": "test-scene-2" }] },
            "p4": { "@apply": [
                { "dataset": "test-dataset-2", "scene": "test-scene-3" },
                { "dataset": "test-dataset-3", "scene": "test-scene-2" },
            ] },
        },
    }
    dataset_metadata = {
        "name": "test-dataset",
        "scene": "test-scene",
    }

    presets = None
    presets = registry.get_presets_to_apply(spec, dataset_metadata, presets)
    assert presets == set(("p1",))

    presets = []
    presets = registry.get_presets_to_apply(spec, dataset_metadata, presets)
    assert presets == set(())

    presets = ["p2"]
    presets = registry.get_presets_to_apply(spec, dataset_metadata, presets)
    assert presets == set(("p2",))

    presets = ["p2", "@auto"]
    presets = registry.get_presets_to_apply(spec, dataset_metadata, presets)
    assert presets == set(("p2", "p1"))

    # Test union conditions
    presets = None
    presets = registry.get_presets_to_apply(spec, {
        "name": "test-dataset-2",
        "scene": "test-scene-3",
    }, presets)
    assert presets == set(("p4",))


def test_get_config_overrides_from_presets():
    from nerfbaselines import registry

    spec: registry.MethodSpec = {
        "method": "TestMethod",
        "conda": { "environment_name": "test", "install_script": "" },
        "id": "test",
        "presets": {
            "p1": { 
               "@apply": [{ "dataset": "test-dataset" }],
               "@description": "Test preset 1",
                "key1": "value1",
                "key2": "value2",
            },
            "p2": { 
               "@apply": [{ "dataset": "test-dataset" }],
                "key1": "value3",
                "key3": "value3",
            },
            "p3": { 
                "key4": "value4",
            },
        },
    }

    # Simple test
    o = registry.get_config_overrides_from_presets(spec, ["p1"])
    assert o == {
        "key1": "value1",
        "key2": "value2",
    }

    # Test override previous preset
    o = registry.get_config_overrides_from_presets(spec, ["p1", "p2"])
    assert o == {
        "key1": "value3",
        "key2": "value2",
        "key3": "value3",
    }
    o = registry.get_config_overrides_from_presets(spec, ["p2", "p1"])
    assert o == {
        "key1": "value3",
        "key2": "value2",
        "key3": "value3",
    }

    # Test override previous preset
    o = registry.get_config_overrides_from_presets(spec, ["p1", "p2", "p3"])
    assert o == {
        "key1": "value3",
        "key2": "value2",
        "key3": "value3",
        "key4": "value4",
    }
