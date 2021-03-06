"""
Creates a MobileNetV2 Model as defined in:
Mark Sandler, Andrew Howard, Menglong Zhu, Andrey Zhmoginov, Liang-Chieh Chen. (2018).
MobileNetV2: Inverted Residuals and Linear Bottlenecks
arXiv preprint arXiv:1801.04381.
import from https://github.com/tonylins/pytorch-mobilenet-v2
"""

from functions import *
import torch.nn as nn
import math
import numpy as np

__all__ = ['mobilenetv2']


def _make_divisible(v, divisor, min_value=None):
    """
    This function is taken from the original tf repo.
    It ensures that all layers have a channel number that is divisible by 8
    It can be seen here:
    https://github.com/tensorflow/models/blob/master/research/slim/nets/mobilenet/mobilenet.py
    :param v:
    :param divisor:
    :param min_value:
    :return:
    """
    if min_value is None:
        min_value = divisor
    new_v = max(min_value, int(v + divisor / 2) // divisor * divisor)
    # Make sure that round down does not go down by more than 10%.
    if new_v < 0.9 * v:
        new_v += divisor
    return new_v


def conv_3x3_bn(inp, oup, stride, is_qt=False, lq=False, fwlq=False): # first layer
    return nn.Sequential(
        nn.Conv2d(inp, oup, 3, stride=stride, padding=1, bias=False),
        nn.BatchNorm2d(oup),
        nn.ReLU6(inplace=True)
    )


def conv_1x1_bn(inp, oup, lq=False, is_qt=False, fwlq=False):
    return nn.Sequential(
        LQ_Conv2d(inp, oup, 1, stride=1, padding=0, bias=False, is_qt=is_qt, lq=lq, fwlq=fwlq),
        nn.BatchNorm2d(oup),
        nn.ReLU6(inplace=True)
    )


class InvertedResidual(nn.Module):
    def __init__(self, inp, oup, stride, expand_ratio, is_qt=False, lq=False, fwlq=False):
        super(InvertedResidual, self).__init__()
        assert stride in [1, 2]

        hidden_dim = round(inp * expand_ratio)
        self.identity = stride == 1 and inp == oup
        self.expand_ratio = expand_ratio

        if expand_ratio == 1:
            self.conv = nn.Sequential(
                # dw
                LQ_Conv2d(inp, hidden_dim, 3, stride=stride, padding=1, groups=hidden_dim, bias=False, is_qt=is_qt, lq=lq, fwlq=fwlq),
                nn.BatchNorm2d(hidden_dim),
                nn.ReLU6(inplace=True),
                # pw-linear
                LQ_Conv2d(hidden_dim, oup, 1, stride=1, padding=0, bias=False, is_qt=is_qt, lq=lq, fwlq=fwlq),
                nn.BatchNorm2d(oup),
            )
        else:
            self.conv = nn.Sequential(
                # pw
                LQ_Conv2d(inp, hidden_dim, 1, stride=1, padding=0, bias=False, is_qt=is_qt, lq=lq, fwlq=fwlq),
                nn.BatchNorm2d(hidden_dim),
                nn.ReLU6(inplace=True),
                # dw
                LQ_Conv2d(hidden_dim, hidden_dim, 3, stride=stride, padding=1, groups=hidden_dim, bias=False, is_qt=is_qt, lq=lq, fwlq=fwlq),
                nn.BatchNorm2d(hidden_dim),
                nn.ReLU6(inplace=True),
                # pw-linear
                LQ_Conv2d(hidden_dim, oup, 1, stride=1, padding=0, bias=False, is_qt=is_qt, lq=lq, fwlq=fwlq),
                nn.BatchNorm2d(oup),
            )

    def forward(self, x):
        if self.expand_ratio == 1:
            out, _ = self.conv[0](x)
            out = self.conv[1:3](out)
            out, _ = self.conv[3](out)
            out = self.conv[4](out)
        else:
            out, _ = self.conv[0](x)
            out = self.conv[1:3](out)
            out, _ = self.conv[3](out)
            out = self.conv[4:6](out)
            out, _ = self.conv[6](out)

        if self.identity:
            return x + out
            
        else:
            return out 


class MobileNetV2(nn.Module):
    def __init__(self, num_classes=1000, is_qt=False, lq=False, fwlq=False, index=[]):
        super(MobileNetV2, self).__init__()
        # setting of inverted residual blocks
        self.cfgs = [
            # t, c, n, s
            [1,  16, 1, 1],
            [6,  24, 2, 2],
            [6,  32, 3, 2],
            [6,  64, 4, 2],
            [6,  96, 3, 1],
            [6, 160, 3, 2],
            [6, 320, 1, 1],
        ]
        # building first layer
        width_mult = 1
        input_channel = _make_divisible(32 * width_mult, 4 if width_mult == 0.1 else 8)
        layers = [conv_3x3_bn(3, input_channel, 2, is_qt=is_qt, lq=lq, fwlq=fwlq)]
        
        # building inverted residual blocks
        block = InvertedResidual
        for t, c, n, s in self.cfgs:
            output_channel = _make_divisible(c * width_mult, 4 if width_mult == 0.1 else 8)
            for i in range(n):
                layers.append(block(input_channel, output_channel, s if i == 0 else 1, t, is_qt=is_qt, lq=lq, fwlq=fwlq))
                input_channel = output_channel
        self.features = nn.Sequential(*layers)
        
        # building last several layers
        output_channel = _make_divisible(1280 * width_mult, 4 if width_mult == 0.1 else 8) if width_mult > 1.0 else 1280
        self.conv = conv_1x1_bn(input_channel, output_channel, is_qt=is_qt, lq=lq, fwlq=fwlq)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.classifier = nn.Linear(output_channel, num_classes) # last layer
        self._initialize_weights()

    def forward(self, x):
        x = self.features(x)
        x, _ = self.conv[0](x)
        x = self.conv[1:](x)
        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.classifier(x)
        return x, torch.Tensor([0]).cuda()

    def _initialize_weights(self):
        for m in self.modules():
            if isinstance(m, lq_conv2d_orig):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
                if m.bias is not None:
                    m.bias.data.zero_()
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.fill_(1)
                m.bias.data.zero_()
            elif isinstance(m, nn.Linear):
                m.weight.data.normal_(0, 0.01)
                m.bias.data.zero_()

def mobilenetv2(**kwargs):
    """
    Constructs a MobileNet V2 model
    """
    return MobileNetV2(**kwargs)


def mobilenetv2_cifar(num_classes, is_qt, lq, index, fwlq):
    return MobileNetV2(num_classes=num_classes, is_qt=is_qt, lq=lq, index=index, fwlq=fwlq)

''''
import torch
import torch.nn as nn
import torch.nn.functional as F


class LinearBottleNeck(nn.Module):

    def __init__(self, in_channels, out_channels, stride, t=6, class_num=100, lq=False):
        super().__init__()

        self.residual = nn.Sequential(
            LQ_Conv2d(in_channels, in_channels * t, 1, lq=lq),
            nn.BatchNorm2d(in_channels * t),
            nn.ReLU6(inplace=True),

            LQ_Conv2d(in_channels * t, in_channels * t, 3, stride=stride, padding=1, groups=in_channels * t, lq=lq),
            nn.BatchNorm2d(in_channels * t),
            nn.ReLU6(inplace=True),

            LQ_Conv2d(in_channels * t, out_channels, 1, lq=lq),
            nn.BatchNorm2d(out_channels)
        )

        self.stride = stride
        self.in_channels = in_channels
        self.out_channels = out_channels

    def forward(self, x):

        residual = self.residual(x)

        if self.stride == 1 and self.in_channels == self.out_channels:
            residual += x

        return residual

class MobileNetV2(nn.Module):

    def __init__(self, num_classes=1000, lq=False):
        super().__init__()

        self.pre = nn.Sequential(
            LQ_Conv2d(3, 32, 1, padding=1, lq=lq),
            nn.BatchNorm2d(32),
            nn.ReLU6(inplace=True)
        )

        self.stage1 = LinearBottleNeck(32, 16, 1, 1)
        self.stage2 = self._make_stage(2, 16, 24, 2, 6)
        self.stage3 = self._make_stage(3, 24, 32, 2, 6)
        self.stage4 = self._make_stage(4, 32, 64, 2, 6)
        self.stage5 = self._make_stage(3, 64, 96, 1, 6)
        self.stage6 = self._make_stage(3, 96, 160, 1, 6)
        self.stage7 = LinearBottleNeck(160, 320, 1, 6)

        self.conv1 = nn.Sequential(
            LQ_Conv2d(320, 1280, 1, lq=lq),
            nn.BatchNorm2d(1280),
            nn.ReLU6(inplace=True)
        )

        self.conv2 = nn.Conv2d(1280, num_classes, 1)

    def forward(self, x):
        x = self.pre(x)
        x = self.stage1(x)
        x = self.stage2(x)
        x = self.stage3(x)
        x = self.stage4(x)
        x = self.stage5(x)
        x = self.stage6(x)
        x = self.stage7(x)
        x = self.conv1(x)
        x = F.adaptive_avg_pool2d(x, 1)
        x = self.conv2(x)
        x = x.view(x.size(0), -1)

        return x

    def _make_stage(self, repeat, in_channels, out_channels, stride, t):

        layers = []
        layers.append(LinearBottleNeck(in_channels, out_channels, stride, t))

        while repeat - 1:
            layers.append(LinearBottleNeck(out_channels, out_channels, 1, t))
            repeat -= 1

        return nn.Sequential(*layers)
'''
