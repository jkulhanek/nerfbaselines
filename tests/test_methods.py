import pytest
from nerfbaselines.registry import supported_methods
from nerfbaselines import registry
from nerfbaselines.datasets import load_dataset
from nerfbaselines.types import Method
from nerfbaselines.backends import Backend
from nerfbaselines.utils import NoGPUError
import tempfile


def test_supported_methods():
    methods = supported_methods()
    assert len(methods) > 0
    assert "nerf" in methods
    assert "mipnerf360" in methods
    assert "instant-ngp" in methods
    assert "gaussian-splatting" in methods
    assert "nerfacto" in methods
    assert "tetra-nerf" in methods
    assert "zipnerf" in methods
    assert "mip-splatting" in methods


@pytest.mark.docker
@pytest.mark.parametrize("method_name", [pytest.param(k, marks=[pytest.mark.method(k)]) for k in supported_methods()])
def test_method_docker(blender_dataset_path, method_name):
    try:
        with registry.build_method(method_name, backend="docker") as method_cls:
            info = method_cls.get_method_info()
            dataset = load_dataset(blender_dataset_path, "train", info["required_features"])
            dataset.load_features(info["required_features"], info["supported_camera_models"])
            assert Backend.current is not None
            assert Backend.current.name == "docker"
            assert method_cls.get_method_info()["name"] == method_name
            with tempfile.TemporaryDirectory() as tmpdir:
                method = method_cls(train_dataset=dataset)
                assert isinstance(method, Method)  # type: ignore
                method.save(tmpdir)

                method = method_cls(checkpoint=tmpdir)
    except NoGPUError:
        pytest.skip("No GPU available")


@pytest.mark.apptainer
@pytest.mark.parametrize("method_name", [pytest.param(k, marks=[pytest.mark.method(k)]) for k in supported_methods()])
def test_method_apptainer(blender_dataset_path, method_name):
    try:
        with registry.build_method(method_name, backend="apptainer") as method_cls:
            info = method_cls.get_method_info()
            dataset = load_dataset(blender_dataset_path, "train", info["required_features"])
            dataset.load_features(info["required_features"], info["supported_camera_models"])
            assert Backend.current is not None
            assert Backend.current.name == "apptainer"
            assert method_cls.get_method_info()["name"] == method_name
            with tempfile.TemporaryDirectory() as tmpdir:
                method = method_cls(train_dataset=dataset)
                assert isinstance(method, Method)  # type: ignore
                method.save(tmpdir)

                method = method_cls(checkpoint=tmpdir)
    except NoGPUError:
        pytest.skip("No GPU available")
