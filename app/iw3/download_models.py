import sys
import io
import os

# Force UTF-8 on Windows and disable tqdm unicode
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    # Désactiver les caractères unicode de tqdm
    os.environ['TQDM_POSITION'] = '0'

# Patcher tqdm AVANT les imports
import tqdm
tqdm.tqdm.monitor_interval = 0

from .zoedepth_model import ZoeDepthModel
from .depth_anything_model import DepthAnythingModel
from .depth_anything_v3_model import DepthAnythingV3MonoModel
from .depth_pro_model import DepthProModel
from .video_depth_anything_model import VideoDepthAnythingModel


def main():
    ZoeDepthModel.force_update()
    DepthAnythingModel.force_update()
    DepthProModel.force_update()
    VideoDepthAnythingModel.force_update()
    DepthAnythingV3MonoModel.force_update()
    if not ZoeDepthModel.has_checkpoint_file("ZoeD_Any_N") and not ZoeDepthModel.has_checkpoint_file("ZoeD_N"):
        ZoeDepthModel("ZoeD_Any_N").load(gpu=-1)


if __name__ == "__main__":
    main()