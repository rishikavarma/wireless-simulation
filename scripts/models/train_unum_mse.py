#!/usr/bin/env python3
"""Train a compact UNUM-like forecaster on selected bucket metrics using bucket MSE."""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch
from torch import nn

from train_unum_buckets import (TRIALS, Scores, atomic_json, baseline, batches,
                                count_windows, fit_input_stats, schema,
                                validation_runs, write_results)


TARGETS = (
    'srtt_bucket',
    'rttvar_bucket',
    'sinr_dl_db_bucket',
    'sinr_ul_db_bucket',
    'tb_dl_rx_sinr_db_mean_bucket',
    'tb_ul_rx_sinr_db_mean_bucket',
    'tb_dl_rx_tb_bytes_sum_bucket',
    'tb_ul_tx_tb_bytes_sum_bucket',
    'tb_dl_rx_mcs_mean_bucket',
    'tb_ul_tx_mcs_mean_bucket',
)


def bucket_prediction(values: torch.Tensor, classes: int) -> torch.Tensor:
    """Integer bucket prediction with a straight-through derivative for MSE training."""
    bounded = values.clamp(0, classes - 1)
    rounded = bounded.round()
    return bounded + (rounded - bounded).detach()


class UnumMSE(nn.Module):
    def __init__(self, bucket, direct, context, horizon, stats):
        super().__init__()
        trial = TRIALS[0]
        self.bucket, self.direct = bucket, direct
        self.context, self.horizon = context, horizon
        self.register_buffer('medians', torch.tensor(stats['median'], dtype=torch.float32))
        self.register_buffer('direct_mean', torch.tensor(stats['mean'], dtype=torch.float32))
        self.register_buffer('direct_scale', torch.tensor(stats['scale'], dtype=torch.float32))
        self.embeddings = nn.ModuleList(nn.Embedding(classes + 1, trial.width)
                                        for _, _, classes in bucket)
        self.direct_projection = nn.Linear(2 * len(direct), trial.width) if direct else None
        self.input_norm = nn.LayerNorm(trial.width)
        self.context_position = nn.Parameter(torch.randn(context, trial.width) * .02)
        self.future_position = nn.Parameter(torch.randn(horizon, trial.width) * .02)
        encoder = nn.TransformerEncoderLayer(trial.width, trial.heads,
            trial.feedforward, trial.dropout, batch_first=True, norm_first=True)
        decoder = nn.TransformerDecoderLayer(trial.width, trial.heads,
            trial.feedforward, trial.dropout, batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(encoder, trial.encoder_layers)
        self.decoder = nn.TransformerDecoder(decoder, trial.decoder_layers)
        self.heads = nn.ModuleList(nn.Linear(trial.width, 1) for _ in bucket)
        for head in self.heads:
            nn.init.zeros_(head.weight)
            nn.init.zeros_(head.bias)

    def forward(self, context_tokens):
        batch = context_tokens.shape[0]
        x = None
        base = []
        for j, (embedding, (position, _, classes)) in enumerate(zip(self.embeddings, self.bucket)):
            plane = context_tokens[:, :, position]
            valid = torch.isfinite(plane)
            ids = torch.where(valid, plane, classes).long().clamp(0, classes)
            piece = embedding(ids)
            x = piece if x is None else x + piece
            mean = torch.where(valid, plane, 0).sum(1) / valid.sum(1).clamp_min(1)
            base.append(torch.where(valid.any(1), mean, self.medians[j]))
        x = x / math.sqrt(len(self.bucket))
        if self.direct:
            direct = torch.stack([context_tokens[:, :, position]
                                  for position, _ in self.direct], -1)
            missing = ~torch.isfinite(direct)
            for j, (_, name) in enumerate(self.direct):
                if name == 'base_rtt':
                    direct[:, :, j] = torch.log1p(torch.clamp_min(direct[:, :, j], 0))
            direct = torch.where(missing, self.direct_mean, direct)
            direct = (direct - self.direct_mean) / self.direct_scale
            x = x + self.direct_projection(torch.cat([direct, missing.float()], -1))
        x = self.input_norm(x + self.context_position)
        causal_context = torch.triu(torch.ones(self.context, self.context,
                                                device=x.device, dtype=torch.bool), 1)
        memory = self.encoder(x, mask=causal_context)
        queries = self.future_position.unsqueeze(0).expand(batch, -1, -1)
        causal_future = torch.triu(torch.ones(self.horizon, self.horizon,
                                               device=x.device, dtype=torch.bool), 1)
        decoded = self.decoder(queries, memory, tgt_mask=causal_future)
        predictions = []
        for j, (head, (_, _, classes)) in enumerate(zip(self.heads, self.bucket)):
            values = base[j][:, None] + head(decoded).squeeze(-1)
            predictions.append(bucket_prediction(values, classes))
        return torch.stack(predictions, -1)


def mse_loss(predictions, future, bucket):
    truth = torch.stack([future[:, :, position] for position, _, _ in bucket], -1)
    valid = torch.isfinite(truth)
    if not valid.any():
        return None
    return ((predictions[valid] - truth[valid]) ** 2).mean()


def score(model, dataset, index, validation, split, batch_size, limit,
          count, names, bucket, stats, device, seed):
    model.eval()
    scores = Scores(bucket, model.horizon)
    with torch.inference_mode():
        for values in batches(dataset, index, split, validation, batch_size,
                              limit, count, seed, names):
            context = values[:, :model.context]
            future = values[:, model.context:model.context + model.horizon]
            prediction = model(torch.from_numpy(context).to(device)).cpu().numpy()
            last, avg = baseline(context, bucket, stats['median'])
            scores.add(future, prediction,
                       np.repeat(last[:, None, :], model.horizon, axis=1),
                       np.repeat(avg[:, None, :], model.horizon, axis=1), bucket)
    return scores


def train(args):
    dataset, output = args.dataset.resolve(), args.output.resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError('output must be outside the dataset')
    index, names, all_bucket, direct, context, horizon = schema(dataset)
    by_name = {name: (position, name, classes) for position, name, classes in all_bucket}
    if set(TARGETS) - set(by_name):
        raise ValueError('missing target buckets: ' + ', '.join(set(TARGETS) - set(by_name)))
    bucket = all_bucket if args.target_set == 'all' else [by_name[name] for name in TARGETS]
    validation, cases = validation_runs(dataset, index, args.validation_fraction, args.seed)
    counts = count_windows(dataset, index, validation)
    output.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device(args.device)
    stats = fit_input_stats(dataset, index, validation, bucket, direct,
                            args.batch_size, counts['train'], names)
    atomic_json(output / 'run-plan.json', dict(dataset=str(dataset), counts=counts,
        targets=[dict(name=name, classes=classes) for _, name, classes in bucket],
        validationCases=sorted(cases), args={k: str(v) if isinstance(v, Path) else v
                                            for k, v in vars(args).items()}, stats=stats))
    model = UnumMSE(bucket, direct, context, horizon, stats).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=.01)
    best, best_epoch, history = math.inf, 0, []
    checkpoint = output / 'compact-mse.pt'
    for epoch in range(1, args.epochs + 1):
        model.train()
        losses = []
        for values in batches(dataset, index, 'train', validation, args.batch_size,
                              args.train_windows_per_epoch, counts['train'], args.seed + epoch, names):
            tensor = torch.from_numpy(values).to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = mse_loss(model(tensor[:, :context]), tensor[:, context:context + horizon], bucket)
            if loss is None:
                continue
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step()
            losses.append(float(loss.detach()))
        if not losses:
            raise ValueError('no observed training targets')
        validation_scores = score(model, dataset, index, validation, 'validation',
            args.batch_size, args.validation_windows, counts['validation'], names,
            bucket, stats, device, args.seed)
        mse = validation_scores.summary()['model']['mse']
        history.append(dict(epoch=epoch, trainMSE=float(np.mean(losses)), validationMSE=mse))
        print('epoch', epoch, 'validation bucket MSE', round(mse, 4), flush=True)
        if mse < best - args.min_improvement:
            best, best_epoch = mse, epoch
            torch.save(dict(state=model.state_dict(), targets=[name for _, name, _ in bucket], stats=stats,
                            context=context, horizon=horizon), checkpoint)
        atomic_json(output / 'tuning.json', history)
        if epoch - best_epoch >= args.patience:
            break
    saved = torch.load(checkpoint, map_location=device, weights_only=True)
    model.load_state_dict(saved['state'])
    scores = score(model, dataset, index, validation, 'test', args.batch_size,
                   args.test_windows, counts['test'], names, bucket, stats, device, args.seed)
    write_results(output, scores, bucket, index)
    atomic_json(output / 'selected-model.json', dict(bestEpoch=best_epoch,
        bestValidationMSE=best, targets=[name for _, name, _ in bucket], checkpoint=str(checkpoint)))
    print(json.dumps(scores.summary(), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=8)
    parser.add_argument('--patience', type=int, default=3)
    parser.add_argument('--min-improvement', type=float, default=.001)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--train-windows-per-epoch', type=int, default=25000)
    parser.add_argument('--validation-windows', type=int, default=10000)
    parser.add_argument('--test-windows', type=int, default=50000)
    parser.add_argument('--validation-fraction', type=float, default=.1)
    parser.add_argument('--learning-rate', type=float, default=2e-4)
    parser.add_argument('--seed', type=int, default=2026)
    parser.add_argument('--device', choices=('cpu', 'cuda', 'mps'), default='cpu')
    parser.add_argument('--target-set', choices=('selected', 'all'), default='selected')
    args = parser.parse_args()
    if min(args.epochs, args.patience, args.batch_size, args.train_windows_per_epoch,
           args.validation_windows, args.test_windows) < 1:
        parser.error('epoch and window limits must be positive')
    train(args)


if __name__ == '__main__':
    main()
