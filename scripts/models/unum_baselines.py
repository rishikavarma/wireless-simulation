"""MLP and LSTM bucket forecasters for the UNUM architecture comparison.

The network backbones follow the MLP/LSTM comparison in UNUM. Inputs, feature
heads, target buckets, and evaluation are adapted to this project's multi-head
token schema so all models can use the same training and test splits.
"""
from __future__ import annotations

import math

import numpy as np
import torch
from torch import nn


class BucketInput(nn.Module):
    """The same per-feature embedding and direct-value input as UnumBuckets."""

    def __init__(self, bucket, direct, context, width, stats):
        super().__init__()
        self.bucket = bucket
        self.direct = direct
        self.context = context
        classes = [count for _, _, count in bucket]
        self.register_buffer('positions', torch.tensor([p for p, _, _ in bucket]))
        self.register_buffer('classes', torch.tensor(classes))
        self.register_buffer('offsets', torch.tensor(
            np.concatenate([[0], np.cumsum(np.asarray(classes)+1)[:-1]])))
        self.register_buffer('direct_mean', torch.tensor(stats['mean'], dtype=torch.float32))
        self.register_buffer('direct_scale', torch.tensor(stats['scale'], dtype=torch.float32))
        self.embedding = nn.EmbeddingBag(sum(classes)+len(classes), width, mode='sum')
        self.direct_projection = nn.Linear(2*len(direct), width) if direct else None
        self.input_norm = nn.LayerNorm(width)
        self.context_position = nn.Parameter(torch.zeros(context, width))
        nn.init.normal_(self.context_position, std=.02)

    def forward(self, tokens):
        batch = tokens.shape[0]
        planes = tokens[:, :, self.positions]
        missing = ~torch.isfinite(planes)
        ids = torch.where(missing, self.classes.to(planes.dtype), planes).long()
        ids = torch.minimum(ids.clamp_min(0), self.classes)
        flat = (ids+self.offsets).reshape(batch*self.context, -1)
        x = self.embedding(flat).reshape(batch, self.context, -1)
        x = x/math.sqrt(len(self.bucket))
        if self.direct:
            direct = torch.stack([tokens[:, :, p] for p, _ in self.direct], -1)
            absent = ~torch.isfinite(direct)
            for j, (_, name) in enumerate(self.direct):
                if name == 'base_rtt':
                    direct[:, :, j] = torch.log1p(torch.clamp_min(direct[:, :, j], 0))
            direct = torch.where(absent, self.direct_mean, direct)
            direct = (direct-self.direct_mean)/self.direct_scale
            x = x+self.direct_projection(torch.cat([direct, absent.float()], -1))
        return self.input_norm(x+self.context_position)


class MLPBuckets(nn.Module):
    """Four hidden ReLU layers over the entire context, then shared bucket heads."""

    def __init__(self, bucket, direct, context, horizon, trial, stats, delta=False):
        super().__init__()
        if delta:
            raise ValueError('MLP baseline predicts absolute buckets')
        width = trial.width
        hidden = 896
        self.context = context
        self.horizon = horizon
        self.sizes = [count for _, _, count in bucket]
        self.input = BucketInput(bucket, direct, context, width, stats)
        layers = [nn.Linear(context*width, hidden), nn.ReLU(), nn.Dropout(trial.dropout)]
        for _ in range(3):
            layers += [nn.Linear(hidden, hidden), nn.ReLU(), nn.Dropout(trial.dropout)]
        layers += [nn.Linear(hidden, horizon*width)]
        self.network = nn.Sequential(*layers)
        self.heads = nn.Linear(width, sum(self.sizes))

    def forward(self, tokens):
        batch = tokens.shape[0]
        embedded = self.input(tokens)
        future = self.network(embedded.reshape(batch, -1)).reshape(batch, self.horizon, -1)
        return list(self.heads(future).split(self.sizes, dim=-1))


class LSTMBuckets(nn.Module):
    """Two-layer, 128-unit LSTM encoder/decoder with per-feature bucket heads."""

    def __init__(self, bucket, direct, context, horizon, trial, stats, delta=False):
        super().__init__()
        if delta:
            raise ValueError('LSTM baseline predicts absolute buckets')
        self.context = context
        self.horizon = horizon
        self.sizes = [count for _, _, count in bucket]
        self.input = BucketInput(bucket, direct, context, trial.width, stats)
        self.encoder = nn.LSTM(trial.width, 128, num_layers=2, batch_first=True)
        self.future_position = nn.Parameter(torch.zeros(horizon, 128))
        self.decoder = nn.LSTM(128, 128, num_layers=2, batch_first=True)
        self.readout = nn.Sequential(
            nn.Linear(128, 1280), nn.ReLU(), nn.Dropout(trial.dropout),
            nn.Linear(1280, sum(self.sizes)))
        nn.init.normal_(self.future_position, std=.02)

    def forward(self, tokens):
        embedded = self.input(tokens)
        _, state = self.encoder(embedded)
        queries = self.future_position.unsqueeze(0).expand(tokens.shape[0], -1, -1)
        future, _ = self.decoder(queries, state)
        return list(self.readout(future).split(self.sizes, dim=-1))
