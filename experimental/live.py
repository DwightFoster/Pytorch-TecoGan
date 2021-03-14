import os
import argparse
import os
import subprocess
import sys
import numpy as np
import torchvision
import cv2
import torch
import torch.nn.functional  as F

sys.path.insert(1, '../code')


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


from PIL import Image
from models import generator, f_net, discriminator
from ops import *

parser = argparse.ArgumentParser()
parser.add_argument('--g_checkpoint', default=None,
                    help='If provided, the generator will be restored from the provided checkpoint')

parser.add_argument('--f_checkpoint', default=None, nargs="?",
                    help='If provided, the fnet will be restored from the provided checkpoint')
parser.add_argument('--num_resblock', type=int, default=16, help='How many residual blocks are there in the generator')
parser.add_argument('--camera', default=0, type=int, help="The opencv index for the camera")
parser.add_argument('--inputsize', default=32, type=int, help="The resolution to load the webcam at")
parser.add_argument('--numresblocks', default=16, type=int, help="The number of resblocks in the generator")

args = parser.parse_args()

Generator = generator(3, args).cuda()
g_checkpoint = torch.load(args.g_checkpoint)
Generator.load_state_dict(g_checkpoint["model_state_dict"])

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.inputsize)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.inputsize)
ret, frame = cap.read()

# Our operations on the frame come here
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
img = torch.from_numpy(gray).permute(2, 1, 0)

img = torchvision.transforms.functional.resize(img, size=(args.inputsize, args.inputsize)).unsqueeze(0) / 255

gen_flow = upscale_four(img * 4.)
cur_flow = gen_flow[:, 0:2].view(1, args.inputsize * 4, args.inputsize * 4, 2).cpu()
input0 = torch.cat(
    (img, torch.zeros(size=(1, 3 * 4 * 4, args.inputsize, args.inputsize),
                      dtype=torch.float32)), dim=1)
# Passing inputs into model and reshaping output
gen_pre_output = Generator(input0.cuda()).cpu()
x = gen_pre_output

print(x.shape)
x1 = torchvision.transforms.functional.to_pil_image(x.squeeze(0))
x1.show()
while (True):
    # Capture frame-by-frame
    ret, frame = cap.read()

    # Our operations on the frame come here
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    next_img = torch.from_numpy(gray).permute(2, 1, 0)
    next_img = torchvision.transforms.functional.resize(next_img, size=(args.inputsize, args.inputsize)).unsqueeze(
        0) / 255

    gen_pre_output_warp = F.grid_sample(gen_pre_output, cur_flow)
    gen_pre_output_reshape = gen_pre_output_warp.view(1, 3, args.inputsize, 4, args.inputsize, 4)
    gen_pre_output_reshape = gen_pre_output_reshape.permute(0, 1, 3, 5, 2, 4)

    gen_pre_output_reshape = torch.reshape(gen_pre_output_reshape,
                                           (1, 3 * 4 * 4, args.inputsize, args.inputsize))
    inputs = torch.cat((next_img, gen_pre_output_reshape), dim=1)
    gen_output = Generator(inputs.cuda()).cpu()
    cur_flow = upscale_four(next_img * 4.)
    cur_flow = cur_flow[:, 0:2].view(1, args.inputsize * 4, args.inputsize * 4, 2).cpu()
    next_img = next_img.cpu()
    gen_pre_output = gen_output

    # Display the resulting frame
    cv2.imshow('frame', gen_output.squeeze(0).permute(2, 1, 0).detach().numpy())
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# When ev erything done, release the capture
cap.release()
cv2.destroyAllWindows()