import dataclasses
import json
import io
import base64
import yaml
from contextlib import contextmanager
import warnings
from functools import partial
import numpy as np
import argparse
import os
import ast
import importlib.util
from typing import cast, Optional
from nerfbaselines import Method, MethodInfo, ModelInfo, Dataset
from nerfbaselines.utils import pad_poses
from typing import Optional, Union
from typing_extensions import Literal, get_origin, get_args

import torch  # type: ignore


def numpy_to_base64(array: np.ndarray) -> str:
    with io.BytesIO() as f:
        np.save(f, array)
        return base64.b64encode(f.getvalue()).decode("ascii")


def numpy_from_base64(data: str) -> np.ndarray:
    with io.BytesIO(base64.b64decode(data)) as f:
        return np.load(f)


def cast_value(tp, value):
    origin = get_origin(tp)
    if origin is Literal:
        for val in get_args(tp):
            try:
                value_casted = cast_value(type(val), value)
                if val == value_casted:
                    return value_casted
            except ValueError:
                pass
            except TypeError:
                pass
        raise TypeError(f"Value {value} is not in {get_args(tp)}")
            
    if origin is Union:
        for t in get_args(tp):
            try:
                return cast_value(t, value)
            except ValueError:
                pass
            except TypeError:
                pass
        raise TypeError(f"Value {value} is not in {tp}")
    if tp is type(None):
        if str(value).lower() == "none":
            return None
        else:
            raise TypeError(f"Value {value} is not None")
    if tp is bool:
        if str(value).lower() in {"true", "1", "yes"}:
            return True
        elif str(value).lower() in {"false", "0", "no"}:
            return False
        else:
            raise TypeError(f"Value {value} is not a bool")
    if tp in {int, float, bool, str}:
        return tp(value)
    if isinstance(value, tp):
        return value
    raise TypeError(f"Cannot cast value {value} to type {tp}")


class gs_Parser:
    def __init__(self, 
                 data_dir: str,
                 factor: int = 1,
                 normalize: bool = False,
                 test_every: int = 8,
                 state = None,
                 dataset: Optional[Dataset] = None):
        assert factor == 1, "Factor must be 1"
        del test_every, data_dir

        if state is not None:
            self.transform = numpy_from_base64(state["transform_base64"])
            self.scene_scale = state["scene_scale"]
            self.points = np.zeros((state["num_points"], 3), dtype=np.float32)
            self.points_rgb = np.zeros((state["num_points"], 3), dtype=np.uint8)
            self.num_train_images = state["num_train_images"]
            self.dataset = None
            return

        self.num_train_images = len(dataset.get("images"))

        # Optional normalize
        from datasets.normalize import (  # type: ignore
            similarity_from_cameras, transform_cameras, transform_points, align_principle_axes
        )
        if normalize:
            points = dataset.get("points3D_xyz")
            camtoworlds = pad_poses(dataset.get("cameras").poses)
            T1 = similarity_from_cameras(camtoworlds)
            camtoworlds = transform_cameras(T1, camtoworlds)
            points = transform_points(T1, points)

            T2 = align_principle_axes(points)
            camtoworlds = transform_cameras(T2, camtoworlds)
            points = transform_points(T2, points)

            transform = cast(np.ndarray, T2 @ T1)

            # Apply transform to the dataset
            dataset = dataset.copy()
            dataset["cameras"] = dataset["cameras"].replace(poses=camtoworlds[..., :3, :4])
            dataset["points3D_xyz"] = points
        else:
            transform = np.eye(4)
        self.transform = transform

        # size of the scene measured by cameras
        camera_locations = dataset["cameras"].poses[:, :3, 3]
        scene_center = np.mean(camera_locations, axis=0)
        dists = np.linalg.norm(camera_locations - scene_center, axis=1)

        self.scene_scale = np.max(dists)
        self.points = dataset.get("points3D_xyz")
        self.points_rgb = dataset.get("points3D_rgb")
        self.dataset = dataset

    def export(self):
        return {
            "scene_scale": self.scene_scale,
            "num_points": len(self.points),
            "transform": self.transform.tolist(),
            "transform_base64": numpy_to_base64(self.transform),
            "num_train_images": self.num_train_images,
        }


class gs_Dataset:
    def __init__(self, parser, 
                 split: str = "train",
                 patch_size: Optional[int] = None,
                 load_depths: bool = False):
        self.parser = parser
        self.split = split
        self.patch_size = patch_size
        self.load_depths = load_depths

    def __len__(self):
        return self.parser.num_train_images

    def __getitem__(self, idx):
        dataset = self.parser.dataset
        image = dataset["images"][idx][..., :3]
        fx, fy, cx, cy = dataset["cameras"][idx].intrinsics
        K = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])
        camtoworlds = pad_poses(dataset["cameras"][idx].poses)

        if self.patch_size is not None:
            # Random crop.
            h, w = image.shape[:2]
            x = np.random.randint(0, max(w - self.patch_size, 1))
            y = np.random.randint(0, max(h - self.patch_size, 1))
            image = image[y : y + self.patch_size, x : x + self.patch_size]
            K[0, 2] -= x
            K[1, 2] -= y

        data = {
            "K": torch.from_numpy(K).float(),
            "camtoworld": torch.from_numpy(camtoworlds).float(),
            "image": torch.from_numpy(image).float(),
            "image_id": idx,  # the index of the image in the dataset
        }

        if self.load_depths:
            # projected points to image plane to get depths
            worldtocams = np.linalg.inv(camtoworlds)
            points_world = dataset["points3D_xyz"][dataset["images_points3D_indides"][idx]]
            points_cam = (worldtocams[:3, :3] @ points_world.T + worldtocams[:3, 3:4]).T
            points_proj = (K @ points_cam.T).T
            points = points_proj[:, :2] / points_proj[:, 2:3]  # (M, 2)
            depths = points_cam[:, 2]  # (M,)
            # filter out points outside the image
            selector = (
                (points[:, 0] >= 0)
                & (points[:, 0] < image.shape[1])
                & (points[:, 1] >= 0)
                & (points[:, 1] < image.shape[0])
                & (depths > 0)
            )
            points = points[selector]
            depths = depths[selector]
            data["points"] = torch.from_numpy(points).float()
            data["depths"] = torch.from_numpy(depths).float()
        return data


# Extract code dynamically
def _build_simple_trainer_module():
    module_spec = importlib.util.find_spec("simple_trainer", __package__)
    with open(module_spec.origin, "r") as f:
        simple_trainer_ast = ast.parse(f.read())

    # Transform simple trainer, set num_workers=0
    class _Visitor(ast.NodeVisitor):
        def visit(self, node):
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "torch.utils.data.DataLoader":
                num_workers = next(x for x in node.keywords if x.arg == "num_workers")
                num_workers.value = ast.Constant(value=0, kind=None, lineno=0, col_offset=0)
                persistent_workers = next((x for x in node.keywords if x.arg == "persistent_workers"), None)
                if persistent_workers is not None:
                    node.keywords.remove(persistent_workers)
            super().visit(node)
    _Visitor().visit(simple_trainer_ast)

    # Filter imports
    simple_trainer_ast.body.remove(next(x for x in simple_trainer_ast.body if ast.unparse(x) == "from torch.utils.tensorboard import SummaryWriter"))
    simple_trainer_ast.body.remove(next(x for x in simple_trainer_ast.body if ast.unparse(x) == "import viser"))
    # simple_trainer_ast.body.remove(next(x for x in simple_trainer_ast.body if ast.unparse(x) == "import nerfview"))

    runner_ast = next(x for x in simple_trainer_ast.body if getattr(x, "name", None) == "Runner")
    runner_train_ast = next(x for x in runner_ast.body if getattr(x, "name", None) == "train")
    runner_train_ast.name = "setup_train"
    # Training loop
    assert isinstance(runner_train_ast.body[-1], ast.For)
    # Train init body - we remove unused code
    init_train_body = list(runner_train_ast.body[:-3])
    init_train_body.pop(4)
    init_train_body.extend(ast.parse("""
self.trainloader=trainloader
self.trainloader_iter=trainloader_iter
self.schedulers=schedulers
""").body)
    iter_step_body = []
    iter_step_body.extend(ast.parse("""
trainloader_iter=self.trainloader_iter
trainloader=self.trainloader
schedulers=self.schedulers
""").body)
    iter_step_body.extend(init_train_body[:4])
    iter_step_body.extend((runner_train_ast.body[-1].body)[1:-1])
    iter_step_body.pop(-10)  # Remove write to tensorboard step
    save_step = iter_step_body.pop(-9)  # Remove save() step
    iter_step_body.pop(-2)  # Remove eval() step
    # Remove pbar.set_description
    iter_step_body.pop(next(i for i, step in enumerate(iter_step_body) if ast.unparse(step) == "pbar.set_description(desc)"))
    iter_step_body.extend(ast.parse("""def _():
    self.trainloader_iter=trainloader_iter
    out={"loss": loss.item(), "l1": l1loss.item(), "ssim": ssimloss.item(), "num_gaussians": len(self.splats["means"])}
    if cfg.depth_loss:
        out["depthloss"] = depthloss.item()
    return out
""").body[0].body)
    runner_train_ast.body = init_train_body
    runner_ast.body.append(ast.FunctionDef(lineno=0, col_offset=0,
        name="train_iteration",
        args=ast.arguments(
            args=[
                ast.arg(arg="self", annotation=None, lineno=0, col_offset=0),
                ast.arg(arg="step", annotation=None, lineno=0, col_offset=0),
            ],
            posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[],
            kwarg=None, kwargannotation=None, return_annotation=None
        ),
        body=iter_step_body, decorator_list=[],
    ))

    # Save method
    save_step_body = save_step.body[4:]  # Strip saving stats
    save_step_body.insert(0, ast.parse("cfg=self.cfg").body[0])
    # Change saving location
    save_step_body[-1].value.args[1].values[0].value = ast.Name(id="path", ctx=ast.Load(), lineno=0, col_offset=0)
    runner_ast.body.append(ast.FunctionDef(lineno=0, col_offset=0,
        name="save",
        args=ast.arguments(
            args=[
                ast.arg(arg="self", annotation=None, lineno=0, col_offset=0),
                ast.arg(arg="step", annotation=None, lineno=0, col_offset=0),
                ast.arg(arg="path", annotation=None, lineno=0, col_offset=0),
            ],
            posonlyargs=[], kwonlyargs=[], kw_defaults=[], defaults=[],
            kwarg=None, kwargannotation=None, return_annotation=None
        ),
        body=save_step_body, decorator_list=[],
    ))

    # Init method
    init_method = next(x for x in runner_ast.body if getattr(x, "name", None) == "__init__")
    init_method.body = init_method.body[:6] + init_method.body[14:-2]  # Remove unused code
    init_method.args.args.append(ast.arg(arg="Parser", annotation=None, lineno=0, col_offset=0))
    init_method.args.args.append(ast.arg(arg="Dataset", annotation=None, lineno=0, col_offset=0))

    # Execute code to build module
    module = {}
    exec(compile(simple_trainer_ast, "<string>", "exec"), module)
    return argparse.Namespace(**module)


class GSplat(Method):
    def __init__(self, *, train_dataset=None, checkpoint=None, config_overrides=None):
        super().__init__()

        self.checkpoint = checkpoint
        self.simple_trainer = _build_simple_trainer_module()

        # Build trainer
        self.cfg = self._get_config(checkpoint, config_overrides)

        # Load parser state
        parser_state = None
        if checkpoint is not None and os.path.exists(f"{checkpoint}/parser.json"):
            with open(f"{checkpoint}/parser.json", "r") as f:
                parser_state = json.load(f)

        # Build runner
        local_rank = world_rank = 0
        world_size = 1
        self.runner = self.simple_trainer.Runner(
            local_rank, world_rank, world_size, self.cfg, Dataset=gs_Dataset, Parser=partial(gs_Parser, dataset=train_dataset, state=parser_state))
        self.step = 0
        self._loaded_step = None

        # Load checkpoint if available
        if checkpoint is not None:
            ckpt_files = [os.path.join(checkpoint, x) for x in os.listdir(checkpoint) if x.startswith("ckpt_") and x.endswith(".pt")]
            ckpt_files.sort(key=lambda x: int(x.split("_rank")[-1].split(".")[0]))
            ckpts = [
                torch.load(file, map_location=self.runner.device, weights_only=True)
                for file in ckpt_files
            ]
            for k in self.runner.splats.keys():
                self.runner.splats[k].data = torch.cat([ckpt["splats"][k] for ckpt in ckpts])
            self.step = self._loaded_step = ckpts[0]["step"]

        # Setup dataloaders if training mode
        if train_dataset is not None:
            self.runner.setup_train()

    def _get_config(self, checkpoint, config_overrides):
        cfg = self.simple_trainer.Config(
            strategy=self.simple_trainer.DefaultStrategy(verbose=True),
        )
        cfg.data_factor = 1

        if checkpoint is not None:
            with open(f"{checkpoint}/cfg.yml", "r") as f:
                cfg_dict = yaml.load(f, Loader=yaml.UnsafeLoader)
            cfg.__dict__.update(cfg_dict)

        # Apply config overrides
        field_types = {k.name: k.type for k in dataclasses.fields(cfg)}
        if "strategy" in (config_overrides or {}):
            import gsplat.strategy as strategies  # type: ignore
            cfg.strategy = getattr(strategies, config_overrides["strategy"])()
        strat_types = {k.name: k.type for k in dataclasses.fields(cfg.strategy)}
        for k, v in (config_overrides or {}).items():
            if k == "strategy":
                continue
            if k.startswith("strategy."):
                v = cast_value(strat_types[k], v)
                setattr(cfg.strategy, k[len("strategy."):], v)
                continue
            v = cast_value(field_types[k], v)
            setattr(cfg, k, v)

        cfg.adjust_steps(cfg.steps_scaler)
        if cfg.pose_opt:
            warnings.warn("Pose optimization is enabled, but it will only by applied to training images. No test-time pose optimization is enabled.")
        return cfg

    @classmethod
    def get_method_info(cls):
        return MethodInfo(
            method_id="",  # Will be filled in by the registry
            required_features=frozenset(("points3D_xyz", "points3D_rgb", "color", "images_points3D_indices")),
            supported_camera_models=frozenset(("pinhole",)),
            supported_outputs=("color",),
        )

    def get_info(self):
        return ModelInfo(
            **self.get_method_info(),
            num_iterations=self.cfg.max_steps,
            loaded_step=self._loaded_step,
            loaded_checkpoint=self.checkpoint,
        )

    def train_iteration(self, step):
        self.step = step
        out = self.runner.train_iteration(step)
        self.step = step+1
        return out

    def save(self, path):
        os.makedirs(path, exist_ok=True)
        with open(f"{path}/cfg.yml", "w") as f:
            yaml.dump(vars(self.cfg), f)
        with open(f"{path}/parser.json", "w") as f:
            json.dump(self.runner.parser.export(), f)
        self.runner.save(self.step, path)

    @contextmanager
    def _patch_embedding(self, embedding=None):
        if embedding is None:
            yield None
            return
        embeds_module = None
        try:
            was_called = False
            if self.cfg.app_opt:
                embeds_module = self.runner.app_module.embed
                def _embed(*args, **kwargs):
                    del args, kwargs
                    nonlocal was_called
                    was_called = True
                    return embedding[None]
                self.runner.app_module.embed = _embed
            yield None
            if self.cfg.app_opt:
                assert was_called, "Failed to patch appearance embedding"
        finally:
            # Return embeds back
            if embeds_module is not None:
                self.runner.app_module.embed = embeds_module

    @torch.no_grad()
    def render(self, camera, *, options=None):
        camera = camera.item()
        from datasets.normalize import transform_cameras  # type: ignore
        cfg = self.cfg
        device = self.runner.device
        # TODO: apply transform
        print(camera.poses.shape)
        camtoworlds_np = transform_cameras(self.runner.parser.transform, pad_poses(camera.poses[None]))
        camtoworlds = torch.from_numpy(camtoworlds_np).float().to(device)
        fx, fy, cx, cy = camera.intrinsics
        Ks = torch.from_numpy(np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]])).float().to(device)
        width, height = camera.image_sizes

        # Patch appearance
        embedding_np = (options or {}).get("embedding")
        embedding = torch.from_numpy(embedding_np).to(self.runner.device) if embedding_np is not None else None
        with self._patch_embedding(embedding):
            colors, _, _ = self.runner.rasterize_splats(
                camtoworlds=camtoworlds,
                Ks=Ks[None],
                width=width,
                height=height,
                sh_degree=cfg.sh_degree,
                near_plane=cfg.near_plane,
                far_plane=cfg.far_plane,
                image_ids=object()
            )  # [1, H, W, 3]
        return {
            "color": torch.clamp(colors.squeeze(0), 0.0, 1.0).detach().cpu().numpy(),
        }

    def get_train_embedding(self, index):
        if not self.cfg.app_opt:
            return None
        return self.runner.app_module.embeds.weight[index].detach().cpu().numpy()

    def optimize_embedding(self, dataset: Dataset, *, embedding=None):
        raise NotImplementedError()
