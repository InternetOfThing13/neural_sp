#! /usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright 2018 Kyoto University (Hirofumi Inaguma)
#  Apache 2.0  (http://www.apache.org/licenses/LICENSE-2.0)

"""CNN encoder."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from collections import OrderedDict
import numpy as np
import torch.nn as nn

from neural_sp.models.model_utils import LinearND


class CNNEncoder(nn.Module):
    """CNN encoder.

    Args:
        input_dim (int): dimension of input features (freq * channel)
        in_channel (int): number of channels of input features
        channels (list): number of channles in CNN layers
        kernel_sizes (list): size of kernels in CNN layers
        strides (list): strides in CNN layers
        poolings (list): size of poolings in CNN layers
        dropout (float): probability to drop nodes in hidden-hidden connection
        batch_norm (bool): if True, apply batch normalization
        residual (bool): add residual connections
        bottleneck_dim (int): dimension of the bottleneck layer after the last layer

    """

    def __init__(self,
                 input_dim,
                 in_channel,
                 channels,
                 kernel_sizes,
                 strides,
                 poolings,
                 dropout,
                 batch_norm=False,
                 residual=False,
                 bottleneck_dim=0):

        super(CNNEncoder, self).__init__()

        self.in_channel = in_channel
        assert input_dim % in_channel == 0
        self.input_freq = input_dim // in_channel
        self.residual = residual
        self.bottleneck_dim = bottleneck_dim

        assert len(channels) > 0
        assert len(channels) == len(kernel_sizes) == len(strides) == len(poolings)

        layers = OrderedDict()
        in_ch = in_channel
        in_freq = self.input_freq
        for l in range(len(channels)):
            # Conv
            conv = nn.Conv2d(in_channels=in_ch,
                             out_channels=channels[l],
                             kernel_size=tuple(kernel_sizes[l]),
                             stride=tuple(strides[l]),
                             padding=(1, 1))
            layers['conv' + str(channels[l]) + '_l' + str(l)] = conv
            in_freq = int(np.floor((in_freq + 2 * conv.padding[1] - conv.kernel_size[1]) / conv.stride[1] + 1))

            # Activation
            layers['relu_' + str(l)] = nn.ReLU()

            # Dropout for hidden-hidden connection
            layers['dropout_' + str(l)] = nn.Dropout(p=dropout)
            # TODO(hirofumi): compare BN before and after ReLU

            # Batch Normalization
            if batch_norm:
                layers['bn_' + str(l)] = nn.BatchNorm2d(channels[l])

            # Max Pooling
            if len(poolings[l]) > 0 and np.prod(poolings[l]) > 1:
                pool = nn.MaxPool2d(kernel_size=tuple(poolings[l]),
                                    stride=tuple(poolings[l]),
                                    padding=(0, 0),
                                    ceil_mode=True)
                layers['pool_' + str(l)] = pool
                # NOTE: If ceil_mode is False, remove last feature when the dimension of features are odd.

                in_freq = int(np.ceil((in_freq + 2 * pool.padding[1] - pool.kernel_size[1]) / pool.stride[1] + 1))

            in_ch = channels[l]

        self._output_dim = int(in_ch * in_freq)

        if bottleneck_dim > 0:
            self.bottleneck = LinearND(self._output_dim, bottleneck_dim)
            self._output_dim = bottleneck_dim

        self.layers = nn.Sequential(layers)
        self.get_conv_out_size = ConvOutSize(self.layers)

    @property
    def output_dim(self):
        return self._output_dim

    def forward(self, xs, xlens):
        """Forward computation.

        Args:
            xs (FloatTensor): `[B, T, input_dim (+Δ, ΔΔ)]`
            xlens (list): A list of length `[B]`
        Returns:
            xs (FloatTensor): `[B, T', feat_dim]`
            xlens (list): A list of length `[B]`

        """
        bs, time, input_dim = xs.size()

        # Reshape to `[B, in_ch, T, input_dim // in_ch]`
        xs = xs.contiguous().view(bs, time, self.in_channel, input_dim // self.in_channel).transpose(2, 1)

        xs = self.layers(xs)  # `[B, out_ch, T, feat_dim]`

        # Collapse feature dimension
        bs, out_ch, time, freq = xs.size()
        xs = xs.transpose(2, 1).contiguous().view(bs, time, -1)

        # Reduce dimension
        if self.bottleneck_dim > 0:
            xs = self.bottleneck(xs)

        # Update xlens
        xlens = [self.get_conv_out_size(xlen, dim=0) for xlen in xlens]  # (time, freq)

        return xs, xlens


class ConvOutSize(object):
    """Return the size of outputs for CNN layers."""

    def __init__(self, conv):
        super(ConvOutSize, self).__init__()
        self.conv = conv

        if self.conv is None:
            raise ValueError

    def __call__(self, size, dim):
        """

        Args:
            size (int):
            dim (int): dim == 0 means frequency dimension, dim == 1 means
                time dimension.
        Returns:
            size (int):

        """
        for m in self.conv._modules.values():
            if type(m) in [nn.Conv2d, nn.MaxPool2d]:
                if type(m) == nn.MaxPool2d:
                    if m.ceil_mode:
                        size = int(np.ceil(
                            (size + 2 * m.padding[dim] - m.kernel_size[dim]) / m.stride[dim] + 1))
                    else:
                        size = int(np.floor(
                            (size + 2 * m.padding[dim] - m.kernel_size[dim]) / m.stride[dim] + 1))
                else:
                    size = int(np.floor(
                        (size + 2 * m.padding[dim] - m.kernel_size[dim]) / m.stride[dim] + 1))
                # assert size >= 1
        return size
