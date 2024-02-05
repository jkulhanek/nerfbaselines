import typing
from collections import OrderedDict
import logging
from pathlib import Path
from typing import Tuple, Optional, Dict
import numpy as np
from ..types import Dataset, DatasetFeature, FrozenSet
from ..utils import Indices
from ..cameras import CameraModel, Cameras
from ._colmap_utils import read_cameras_binary, read_images_binary, read_points3D_binary, qvec2rotmat
from ._colmap_utils import read_cameras_text, read_images_text, read_points3D_text, Image, Camera, Point3D
from ._common import DatasetNotFoundError, padded_stack


def _parse_colmap_camera_params(camera: Camera) -> Tuple[np.ndarray, int, np.ndarray, Tuple[int, int]]:
    """
    Parses all currently supported COLMAP cameras into the transforms.json metadata

    Args:
        camera: COLMAP camera
    Returns:
        transforms.json metadata containing camera's intrinsics and distortion parameters

    """
    # Parameters match https://github.com/colmap/colmap/blob/dev/src/base/camera_models.h
    out = OrderedDict()  # Default in Python 3.7+
    camera_params = camera.params
    camera_model: CameraModel
    if camera.model == "SIMPLE_PINHOLE":
        # du = 0
        # dv = 0
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[0])
        cx = float(camera_params[1])
        cy = float(camera_params[2])
        camera_model = CameraModel.PINHOLE
    elif camera.model == "PINHOLE":
        # f, cx, cy, k

        # du = 0
        # dv = 0
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[1])
        cx = float(camera_params[2])
        cy = float(camera_params[3])
        camera_model = CameraModel.PINHOLE
    elif camera.model == "SIMPLE_RADIAL":
        # f, cx, cy, k

        # r2 = u**2 + v**2;
        # radial = k * r2
        # du = u * radial
        # dv = u * radial
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[0])
        cx = float(camera_params[1])
        cy = float(camera_params[2])
        out["k1"] = float(camera_params[3])
        camera_model = CameraModel.OPENCV
    elif camera.model == "RADIAL":
        # f, cx, cy, k1, k2

        # r2 = u**2 + v**2;
        # radial = k1 * r2 + k2 * r2 ** 2
        # du = u * radial
        # dv = v * radial
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[0])
        cx = float(camera_params[1])
        cy = float(camera_params[2])
        out["k1"] = float(camera_params[3])
        out["k2"] = float(camera_params[4])
        camera_model = CameraModel.OPENCV
    elif camera.model == "OPENCV":
        # fx, fy, cx, cy, k1, k2, p1, p2

        # uv = u * v;
        # r2 = u**2 + v**2
        # radial = k1 * r2 + k2 * r2 ** 2
        # du = u * radial + 2 * p1 * u*v + p2 * (r2 + 2 * u**2)
        # dv = v * radial + 2 * p2 * u*v + p1 * (r2 + 2 * v**2)
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[1])
        cx = float(camera_params[2])
        cy = float(camera_params[3])
        out["k1"] = float(camera_params[4])
        out["k2"] = float(camera_params[5])
        out["p1"] = float(camera_params[6])
        out["p2"] = float(camera_params[7])
        camera_model = CameraModel.OPENCV
    elif camera.model == "OPENCV_FISHEYE":
        # fx, fy, cx, cy, k1, k2, k3, k4

        # r = sqrt(u**2 + v**2)

        # if r > eps:
        #    theta = atan(r)
        #    theta2 = theta ** 2
        #    theta4 = theta2 ** 2
        #    theta6 = theta4 * theta2
        #    theta8 = theta4 ** 2
        #    thetad = theta * (1 + k1 * theta2 + k2 * theta4 + k3 * theta6 + k4 * theta8)
        #    du = u * thetad / r - u;
        #    dv = v * thetad / r - v;
        # else:
        #    du = dv = 0
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[1])
        cx = float(camera_params[2])
        cy = float(camera_params[3])
        out["k1"] = float(camera_params[4])
        out["k2"] = float(camera_params[5])
        out["k3"] = float(camera_params[6])
        out["k4"] = float(camera_params[7])
        camera_model = CameraModel.OPENCV_FISHEYE
    elif camera.model == "FULL_OPENCV":
        # fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, k5, k6

        # u2 = u ** 2
        # uv = u * v
        # v2 = v ** 2
        # r2 = u2 + v2
        # r4 = r2 * r2
        # r6 = r4 * r2
        # radial = (1 + k1 * r2 + k2 * r4 + k3 * r6) /
        #          (1 + k4 * r2 + k5 * r4 + k6 * r6)
        # du = u * radial + 2 * p1 * uv + p2 * (r2 + 2 * u2) - u
        # dv = v * radial + 2 * p2 * uv + p1 * (r2 + 2 * v2) - v
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[1])
        cx = float(camera_params[2])
        cy = float(camera_params[3])
        out["k1"] = float(camera_params[4])
        out["k2"] = float(camera_params[5])
        out["p1"] = float(camera_params[6])
        out["p2"] = float(camera_params[7])
        out["k3"] = float(camera_params[8])
        out["k4"] = float(camera_params[9])
        out["k5"] = float(camera_params[10])
        out["k6"] = float(camera_params[11])
        raise NotImplementedError(f"{camera.model} camera model is not supported yet!")
    elif camera.model == "FOV":
        # fx, fy, cx, cy, omega
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[1])
        cx = float(camera_params[2])
        cy = float(camera_params[3])
        out["omega"] = float(camera_params[4])
        raise NotImplementedError(f"{camera.model} camera model is not supported yet!")
    elif camera.model == "SIMPLE_RADIAL_FISHEYE":
        # f, cx, cy, k

        # r = sqrt(u ** 2 + v ** 2)
        # if r > eps:
        #     theta = atan(r)
        #     theta2 = theta ** 2
        #     thetad = theta * (1 + k * theta2)
        #     du = u * thetad / r - u;
        #     dv = v * thetad / r - v;
        # else:
        #     du = dv = 0
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[0])
        cx = float(camera_params[1])
        cy = float(camera_params[2])
        out["k1"] = float(camera_params[3])
        camera_model = CameraModel.OPENCV_FISHEYE
    elif camera.model == "RADIAL_FISHEYE":
        # f, cx, cy, k1, k2

        # r = sqrt(u ** 2 + v ** 2)
        # if r > eps:
        #     theta = atan(r)
        #     theta2 = theta ** 2
        #     theta4 = theta2 ** 2
        #     thetad = theta * (1 + k * theta2)
        #     thetad = theta * (1 + k1 * theta2 + k2 * theta4)
        #     du = u * thetad / r - u;
        #     dv = v * thetad / r - v;
        # else:
        #     du = dv = 0
        fl_x = float(camera_params[0])
        fl_y = float(camera_params[0])
        cx = float(camera_params[1])
        cy = float(camera_params[2])
        out["k1"] = float(camera_params[3])
        out["k2"] = float(camera_params[4])
        out["k3"] = 0.0
        out["k4"] = 0.0
        camera_model = CameraModel.OPENCV_FISHEYE
    else:
        # THIN_PRISM_FISHEYE not supported!
        raise NotImplementedError(f"{camera.model} camera model is not supported yet!")

    image_width: int = camera.width
    image_height: int = camera.height
    intrinsics = np.array([fl_x, fl_y, cx, cy], dtype=np.float32) / float(image_width)
    distortion_params = np.array([out.get(k, 0.0) for k in ("k1", "k2", "p1", "p2", "k3", "k4")], dtype=np.float32)
    return intrinsics, camera_model.value, distortion_params, (image_width, image_height)


def load_colmap_dataset(path: Path, 
        images_path: Optional[Path] = None, 
        split: Optional[str] = None, 
        test_indices: Optional[Indices] = None,
        features: Optional[FrozenSet[DatasetFeature]] = None,
        colmap_path: Optional[Path] = None):
    if features is None:
        features = typing.cast(FrozenSet[DatasetFeature], {})
    load_points = "points3D_xyz" in features or "points3D_rgb" in features
    if split:
        assert split in {"train", "test"}
    # Load COLMAP dataset
    if colmap_path is None:
        colmap_path = Path("sparse") / "0"
    colmap_path = path / colmap_path
    if images_path is None:
        images_path = Path("images")
    images_path = path / images_path
    if not colmap_path.exists():
        raise DatasetNotFoundError("Missing 'sparse/0' folder in COLMAP dataset")
    if not (colmap_path / "cameras.bin").exists() and not (colmap_path / "cameras.txt").exists():
        raise DatasetNotFoundError("Missing 'sparse/0/cameras.{bin,txt}' file in COLMAP dataset")
    if not images_path.exists():
        raise DatasetNotFoundError("Missing 'images' folder in COLMAP dataset")

    if (colmap_path / "cameras.bin").exists():
        cameras = read_cameras_binary(colmap_path / "cameras.bin")
    elif (colmap_path / "cameras.txt").exists():
        cameras = read_cameras_text(colmap_path / "cameras.txt")
    else:
        raise DatasetNotFoundError("Missing 'sparse/0/cameras.{bin,txt}' file in COLMAP dataset")

    if not (colmap_path / "images.bin").exists() and not (colmap_path / "images.txt").exists():
        raise DatasetNotFoundError("Missing 'sparse/0/images.{bin,txt}' file in COLMAP dataset")
    if (colmap_path / "images.bin").exists():
        images = read_images_binary(colmap_path / "images.bin")
    elif (colmap_path / "images.txt").exists():
        images = read_images_text(colmap_path / "images.txt")
    else:
        raise DatasetNotFoundError("Missing 'sparse/0/images.{bin,txt}' file in COLMAP dataset")

    points3D: Optional[Dict[str, Point3D]] = None
    if load_points:
        if not (colmap_path / "points3D.bin").exists() and not (colmap_path / "points3D.txt").exists():
            raise DatasetNotFoundError("Missing 'sparse/0/points3D.{bin,txt}' file in COLMAP dataset")
        if (colmap_path / "points3D.bin").exists():
            points3D = read_points3D_binary(colmap_path / "points3D.bin")
        elif (colmap_path / "points3D.txt").exists():
            points3D = read_points3D_text(colmap_path / "points3D.txt")
        else:
            raise DatasetNotFoundError("Missing 'sparse/0/points3D.{bin,txt}' file in COLMAP dataset")

    # Convert to tensors
    camera_intrinsics = []
    camera_poses = []
    camera_types = []
    camera_distortion_params = []
    image_names = []
    camera_sizes = []

    image: Image
    i = 0
    c2w: np.ndarray
    for image in images.values():
        camera: Camera = cameras[image.camera_id]
        intrinsics, camera_type, distortion_params, (w, h) = _parse_colmap_camera_params(camera)
        camera_sizes.append(np.array((w, h), dtype=np.int32))
        camera_intrinsics.append(intrinsics)
        camera_types.append(camera_type)
        camera_distortion_params.append(distortion_params)
        image_names.append(images_path / image.name)

        rotation = qvec2rotmat(image.qvec).astype(np.float32)

        translation = image.tvec.reshape(3, 1).astype(np.float32)
        w2c = np.concatenate([rotation, translation], 1)
        w2c = np.concatenate([w2c, np.array([[0, 0, 0, 1]], dtype=w2c.dtype)], 0)
        c2w = np.linalg.inv(w2c)

        # Convert from COLMAP's camera coordinate system (OpenCV) to ours (OpenGL)
        c2w[0:3, 1:3] *= -1
        # c2w = c2w[np.array([1, 0, 2, 3]), :]
        # c2w[2, :] *= -1
        camera_poses.append(c2w[0:3, :])
        i += 1

    # Estimate nears fars
    near = 0.01
    far = np.stack([x[:3, -1] for x in camera_poses], 0)
    far = float(np.percentile(np.linalg.norm(far - np.mean(far, keepdims=True, axis=0), axis=-1), 90, axis=0))
    nears_fars = np.array([[near, far]] * len(camera_poses), dtype=np.float32)

    # Load points
    points3D_xyz = None
    points3D_rgb = None
    if load_points:
        assert points3D is not None, "3D points have not been loaded"
        points3D_xyz = np.array([p.xyz for p in points3D.values()], dtype=np.float32)
        points3D_rgb = np.array([p.rgb for p in points3D.values()], dtype=np.uint8)

    # camera_ids=torch.tensor(camera_ids, dtype=torch.int32),
    cameras = Cameras(
        poses=np.stack(camera_poses, 0).astype(np.float32),
        normalized_intrinsics=np.stack(camera_intrinsics, 0).astype(np.float32),
        camera_types=np.array(camera_types, dtype=np.int32),
        distortion_parameters=padded_stack(camera_distortion_params).astype(np.float32),
        image_sizes=np.stack(camera_sizes, 0).astype(np.int32),
        nears_fars=nears_fars,
    )
    dataset = Dataset(
        cameras=cameras,
        file_paths=[images_path / x for x in image_names],
        points3D_xyz=points3D_xyz,
        points3D_rgb=points3D_rgb,
        sampling_mask_paths=None,
        file_paths_root=images_path,
    )

    if split is not None:
        if test_indices is None and ((path / "train_list.txt").exists() or (path / "test_list.txt").exists()):
            logging.info(f"colmap dataloader is loading split data from {path / f'{split}_list.txt'}")
            split_image_names = set((path / f"{split}_list.txt").read_text().splitlines())
            indices: np.ndarray = np.ndarray([name in split_image_names for i, name in enumerate(image_names)], dtype=bool)
            if indices.sum() == 0:
                raise DatasetNotFoundError(f"no images found for split {split} in {path / f'{split}_list.txt'}")
            if indices.sum() < len(split_image_names):
                logging.warning(f"only {indices.sum()} images found for split {split} in {path / f'{split}_list.txt'}")
        else:
            if test_indices is None:
                test_indices = Indices.every_iters(8)
            test_indices.total = len(dataset)
            test_indices_array: np.ndarray = np.array([i in test_indices for i in range(len(dataset))], dtype=bool)
            if split == "train":
                indices = np.logical_not(test_indices_array)
            else:
                indices = test_indices_array
        dataset = dataset[indices]
    dataset.metadata["name"] = "colmap"
    return dataset
