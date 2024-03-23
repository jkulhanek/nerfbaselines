import os
from ..registry import register, MethodSpec


official_results = {
    # Blender
    "blender/chair": {"psnr": 35.76, "ssim": 0.985, "lpips": 0.022},
    "blender/drums": {"psnr": 26.01, "ssim": 0.937, "lpips": 0.073},
    "blender/ficus": {"psnr": 33.99, "ssim": 0.982, "lpips": 0.022},
    "blender/hotdog": {"psnr": 37.41, "ssim": 0.982, "lpips": 0.032},
    "blender/lego": {"psnr": 36.46, "ssim": 0.983, "lpips": 0.018},
    "blender/materials": {"psnr": 30.12, "ssim": 0.952, "lpips": 0.058},
    "blender/mic": {"psnr": 34.61, "ssim": 0.988, "lpips": 0.015},
    "blender/ship": {"psnr": 30.77, "ssim": 0.895, "lpips": 0.138},
    # LLFF
    "llff/room": {"psnr": 32.35, "ssim": 0.952, "lpips": 0.167},
    "llff/fern": {"psnr": 25.27, "ssim": 0.814, "lpips": 0.237},
    "llff/leaves": {"psnr": 21.30, "ssim": 0.752, "lpips": 0.217},
    "llff/fortress": {"psnr": 31.36, "ssim": 0.897, "lpips": 0.148},
    "llff/orchids": {"psnr": 19.87, "ssim": 0.649, "lpips": 0.278},
    "llff/flower": {"psnr": 28.60, "ssim": 0.871, "lpips": 0.169},
    "llff/trex": {"psnr": 26.97, "ssim": 0.900, "lpips": 0.221},
    "llff/horns": {"psnr": 28.14, "ssim": 0.877, "lpips": 0.196},
}


TensoRFSpec: MethodSpec = {
    "method": "._impl.tensorf:TensoRF",
    "conda": {
        "environment_name": os.path.split(__file__[:-3])[-1].replace("_", "-"),
        "python_version": "3.8",
        "install_script": """# Install TensoRF
git clone https://github.com/apchenstu/TensoRF.git tensorf
cd tensorf
git checkout 9370a87c88bf41b309da694833c81845cc960d50

conda install -y conda-build
conda develop .

conda install -y pytorch==1.13.1 torchvision==0.14.1 torchaudio==0.13.1 pytorch-cuda=11.7 -c pytorch -c nvidia
conda install -y cudatoolkit-dev=11.7 -c conda-forge
pip install tqdm scikit-image opencv-python configargparse lpips imageio-ffmpeg kornia lpips tensorboard
pip install plyfile six
""",
    },
    "metadata": {
        "name": "TensoRF",
        "description": """TensoRF factorizes the radiance field into a multiple compact low-rank tensor components. It was designed and tester primarily on Blender, LLFF, and NSVF datasets.""",
        "paper_title": "TensoRF: Tensorial Radiance Fields",
        "paper_authors": [
            "Anpei Chen",
            "Zexiang Xu",
            "Andreas Geiger",
            "Jingyi Yu",
            "Hao Su",
        ],
        "paper_link": "https://arxiv.org/pdf/2203.09517.pdf",
        "paper_results": official_results,
        "link": "https://apchenstu.github.io/TensoRF/",
    },
}

register(TensoRFSpec, name="tensorf")
