#!/usr/bin/env python3
"""Train a small UNUM-like bucket forecaster and compare causal baselines.

This file is preparation only. It does nothing until invoked with a finalized
stratified-run dataset produced by scripts/dataset/materialize_windows.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


@dataclass(frozen=True)
class Trial:
    name: str
    width: int
    heads: int
    encoder_layers: int
    decoder_layers: int
    feedforward: int
    dropout: float
    learning_rate: float
    ordinal_weight: float


TRIALS = (
    Trial('compact', 64, 4, 2, 2, 128, .1, 2e-4, .1),
    Trial('balanced', 96, 4, 3, 2, 192, .1, 1e-4, .2),
    Trial('wide', 128, 8, 3, 3, 256, .1, 1e-4, .2),
)


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def schema(dataset: Path):
    index = json.loads((dataset/'dataset-index.json').read_text())
    if index.get('format') != 'sharded-npz-v1':
        raise ValueError('use a finalized sharded dataset')
    if index.get('splitUnit') not in ('stratified-run', 'profile'):
        raise ValueError('use a stratified-run or legacy profile-held-out dataset')
    if not (dataset/'post-complete.json').is_file():
        raise ValueError('dataset is not finalized')
    context, horizon = int(index['context']), int(index['future'])
    if context < 1 or horizon < 1:
        raise ValueError('context and prediction length must be positive')
    names = list(index['feature_names'])
    if len(names) != len(set(names)):
        raise ValueError('duplicate token feature names')
    boundaries = json.loads((dataset/'dataset-boundaries.json').read_text())
    bucket = [(position, name, len(boundaries[name[:-7]]) + 1)
              for position, name in enumerate(names) if name.endswith('_bucket')]
    if not bucket:
        raise ValueError('dataset has no bucketized features')
    direct = [(position, name) for position, name in enumerate(names)
              if not name.endswith('_bucket')]
    return index, names, bucket, direct, context, horizon


def validation_runs(dataset: Path, index: dict, fraction: float, seed: int):
    """Return validation run IDs and their cases.

    Stratified datasets already materialize a validation split. Legacy
    profile-held-out datasets still carve validation profiles from train.
    """
    source = Path(index['sourceDataset'])
    if not source.is_absolute():
        source = (dataset/source).resolve()
    if not (source/'manifest.json').is_file():
        source = dataset.parent
    if not (source/'manifest.json').is_file():
        raise ValueError('source manifest is missing beside the dataset')
    manifest = json.loads((source/'manifest.json').read_text())
    by_run = {str(row['prefix']): str(row['case']) for row in manifest['runs']}
    train_runs = set(index['splits']['train']['runs'])
    test_runs = set(index['splits']['test']['runs'])
    if 'validation' in index['splits']:
        validation = set(index['splits']['validation']['runs'])
        if train_runs & test_runs or train_runs & validation or test_runs & validation:
            raise ValueError('overlapping stratified train/validation/test runs')
        if not train_runs or not validation or not test_runs:
            raise ValueError('invalid stratified train/validation/test split')
        if not train_runs | validation | test_runs <= set(by_run):
            raise ValueError('dataset run missing from source manifest')
        cases = sorted({by_run[run_id] for run_id in validation})
        return validation, cases
    if train_runs & test_runs or not train_runs or not test_runs:
        raise ValueError('invalid train/test run split')
    if not train_runs | test_runs <= set(by_run):
        raise ValueError('dataset run missing from source manifest')
    train_cases = sorted({by_run[run_id] for run_id in train_runs})
    test_cases = {by_run[run_id] for run_id in test_runs}
    if set(train_cases) & test_cases:
        raise ValueError('profile leakage between train and test')
    if len(train_cases) < 2:
        raise ValueError('need at least two training profiles for validation')
    rng = np.random.default_rng(seed)
    rng.shuffle(train_cases)
    count = min(len(train_cases)-1, max(1, round(len(train_cases)*fraction)))
    chosen = set(train_cases[:count])
    return {run_id for run_id in train_runs if by_run[run_id] in chosen}, chosen


def shard_paths(dataset: Path, index: dict, split: str):
    for entry in index['splits'][split]['shards']:
        path = (dataset/entry['path']).resolve()
        if not path.is_relative_to(dataset.resolve()) or not path.is_file():
            raise ValueError('missing or unsafe dataset shard: ' + str(path))
        yield path


def count_windows(dataset: Path, index: dict, validation: set[str]):
    counts = dict(train=0, validation=0, test=0)
    if 'validation' in index['splits']:
        for split in ('train', 'validation', 'test'):
            for path in shard_paths(dataset, index, split):
                with np.load(path, allow_pickle=False) as saved:
                    counts[split] += len(saved['run_ids'])
    else:
        for split in ('train', 'test'):
            for path in shard_paths(dataset, index, split):
                with np.load(path, allow_pickle=False) as saved:
                    ids = saved['run_ids']
                if split == 'test':
                    counts['test'] += len(ids)
                else:
                    count = int(np.isin(ids, list(validation)).sum())
                    counts['validation'] += count
                    counts['train'] += len(ids)-count
    if min(counts.values()) == 0:
        raise ValueError('train, validation and test must all contain windows')
    return counts


def batches(dataset: Path, index: dict, split: str, validation: set[str],
            batch_size: int, limit: int | None, count: int, seed: int,
            expected_names: list[str]):
    rng = np.random.default_rng(seed)
    if 'validation' in index['splits']:
        source_split = split
    else:
        source_split = 'test' if split == 'test' else 'train'
    paths = list(shard_paths(dataset, index, source_split))
    if split == 'train':
        rng.shuffle(paths)
    fraction = min(1., limit/count) if limit else 1.
    for path in paths:
        with np.load(path, allow_pickle=False) as saved:
            names = [str(name) for name in saved['feature_names']]
            if names != expected_names:
                raise ValueError('shard feature order changed: ' + str(path))
            ids = saved['run_ids']
            if 'validation' in index['splits'] or source_split == 'test':
                selected = np.arange(len(ids))
            else:
                in_validation = np.isin(ids, list(validation))
                selected = np.flatnonzero(in_validation if split == 'validation'
                                          else ~in_validation)
            if len(selected) == 0:
                continue
            if fraction < 1:
                selected = rng.choice(selected, max(1, round(len(selected)*fraction)),
                                      replace=False)
            if split == 'train':
                rng.shuffle(selected)
            values = saved['sequences']
            if values.ndim != 3 or values.shape[2] != len(expected_names):
                raise ValueError('invalid sequence shape: ' + str(path))
            for start in range(0, len(selected), batch_size):
                yield np.asarray(values[selected[start:start+batch_size]],
                                 dtype=np.float32)


def fit_input_stats(dataset, index, validation, bucket, direct, batch_size,
                    count, names):
    hist = [np.zeros(classes, dtype=np.int64) for _, _, classes in bucket]
    sums = np.zeros(len(direct), dtype=np.float64)
    squares = np.zeros(len(direct), dtype=np.float64)
    seen = np.zeros(len(direct), dtype=np.int64)
    for values in batches(dataset, index, 'train', validation, batch_size,
                          None, count, 0, names):
        context = values[:, :index['context']]
        for j, (position, name, classes) in enumerate(bucket):
            plane = context[:, :, position]
            valid = plane[np.isfinite(plane)]
            if len(valid):
                if np.any(valid != np.floor(valid)) or np.any(valid < 0) or np.any(valid >= classes):
                    raise ValueError('invalid bucket IDs in ' + name)
                hist[j] += np.bincount(valid.astype(np.int64), minlength=classes)
        for j, (position, name) in enumerate(direct):
            plane = context[:, :, position]
            valid = plane[np.isfinite(plane)]
            if name == 'base_rtt':
                valid = np.log1p(np.maximum(valid, 0))
            sums[j] += valid.sum(dtype=np.float64)
            squares[j] += np.square(valid, dtype=np.float64).sum()
            seen[j] += len(valid)
    median = []
    for values in hist:
        median.append(int(np.searchsorted(np.cumsum(values), (values.sum()+1)//2))
                      if values.sum() else 0)
    mean = np.divide(sums, seen, out=np.zeros_like(sums), where=seen > 0)
    variance = np.divide(squares, seen, out=np.zeros_like(sums), where=seen > 0)-mean**2
    scale = np.sqrt(np.maximum(variance, 1e-8))
    return dict(median=median, mean=mean.tolist(), scale=scale.tolist())


class UnumBuckets(nn.Module):
    """Causal context encoder, future-query decoder, one head per feature."""
    def __init__(self, bucket, direct, context, horizon, trial: Trial, stats):
        super().__init__()
        self.bucket = bucket
        self.direct = direct
        self.context = context
        self.horizon = horizon
        self.register_buffer('direct_mean', torch.tensor(stats['mean'], dtype=torch.float32))
        self.register_buffer('direct_scale', torch.tensor(stats['scale'], dtype=torch.float32))
        self.embeddings = nn.ModuleList(nn.Embedding(classes+1, trial.width)
                                        for _, _, classes in bucket)
        self.direct_projection = nn.Linear(2*len(direct), trial.width) if direct else None
        self.input_norm = nn.LayerNorm(trial.width)
        self.context_position = nn.Parameter(torch.zeros(context, trial.width))
        self.future_position = nn.Parameter(torch.zeros(horizon, trial.width))
        encoder = nn.TransformerEncoderLayer(trial.width, trial.heads,
            trial.feedforward, trial.dropout, batch_first=True, norm_first=True)
        decoder = nn.TransformerDecoderLayer(trial.width, trial.heads,
            trial.feedforward, trial.dropout, batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(encoder, trial.encoder_layers)
        self.decoder = nn.TransformerDecoder(decoder, trial.decoder_layers)
        self.heads = nn.ModuleList(nn.Linear(trial.width, classes)
                                   for _, _, classes in bucket)
        nn.init.normal_(self.context_position, std=.02)
        nn.init.normal_(self.future_position, std=.02)

    def forward(self, context_tokens):
        batch = context_tokens.shape[0]
        x = None
        for embedding, (position, _, classes) in zip(self.embeddings, self.bucket):
            plane = context_tokens[:, :, position]
            missing = ~torch.isfinite(plane)
            ids = torch.where(missing, classes, plane.long()).clamp(0, classes)
            piece = embedding(ids)
            x = piece if x is None else x + piece
        x = x / math.sqrt(len(self.bucket))
        if self.direct:
            direct = torch.stack([context_tokens[:, :, position]
                                  for position, _ in self.direct], -1)
            missing = ~torch.isfinite(direct)
            for j, (_, name) in enumerate(self.direct):
                if name == 'base_rtt':
                    direct[:, :, j] = torch.log1p(torch.clamp_min(direct[:, :, j], 0))
            direct = torch.where(missing, self.direct_mean, direct)
            direct = (direct-self.direct_mean)/self.direct_scale
            x = x + self.direct_projection(torch.cat([direct, missing.float()], -1))
        x = self.input_norm(x + self.context_position)
        causal_context = torch.triu(torch.ones(self.context, self.context,
                                                device=x.device, dtype=torch.bool), 1)
        memory = self.encoder(x, mask=causal_context)
        queries = self.future_position.unsqueeze(0).expand(batch, -1, -1)
        causal_future = torch.triu(torch.ones(self.horizon, self.horizon,
                                               device=x.device, dtype=torch.bool), 1)
        decoded = self.decoder(queries, memory, tgt_mask=causal_future)
        return [head(decoded) for head in self.heads]


def objective(logits, future, bucket, ordinal_weight):
    terms = []
    for predicted, (position, _, classes) in zip(logits, bucket):
        target = future[:, :, position]
        valid = torch.isfinite(target)
        if not valid.any():
            continue
        actual = target[valid].long()
        loss = F.cross_entropy(predicted[valid], actual)
        if ordinal_weight:
            indices = torch.arange(classes, device=predicted.device, dtype=torch.float32)
            expected = (predicted[valid].softmax(-1)*indices).sum(-1)
            loss = loss + ordinal_weight*F.mse_loss(expected, actual.float())/max(1, classes-1)**2
        terms.append(loss)
    if not terms:
        return None
    return torch.stack(terms).mean()


def baseline(context, bucket, medians):
    last, mean = [], []
    for j, (position, _, classes) in enumerate(bucket):
        plane = context[:, :, position]
        valid = np.isfinite(plane)
        last_value = np.where(valid[:, -1], plane[:, -1], medians[j])
        count = valid.sum(axis=1)
        avg = np.divide(np.where(valid, plane, 0).sum(axis=1), count,
                        out=np.full(len(plane), medians[j], dtype=np.float32),
                        where=count > 0)
        last.append(np.clip(np.rint(last_value), 0, classes-1))
        mean.append(np.clip(np.rint(avg), 0, classes-1))
    return np.stack(last, axis=-1), np.stack(mean, axis=-1)


class Scores:
    def __init__(self, features, horizon):
        self.names = [name for _, name, _ in features]
        self.methods = ('model', 'persistence', 'context_mean')
        self.sq = {method: np.zeros((len(features), horizon), dtype=np.float64)
                   for method in self.methods}
        self.abs = {method: np.zeros((len(features), horizon), dtype=np.float64)
                    for method in self.methods}
        self.exact = {method: np.zeros((len(features), horizon), dtype=np.int64)
                      for method in self.methods}
        self.count = np.zeros((len(features), horizon), dtype=np.int64)
        self.total = np.zeros((len(features), horizon), dtype=np.int64)

    def add(self, values, model, persistence, context_mean, bucket):
        for j, (position, _, _) in enumerate(bucket):
            truth = values[:, :, position]
            valid = np.isfinite(truth)
            self.count[j] += valid.sum(axis=0)
            self.total[j] += len(truth)
            for method, prediction in zip(self.methods,
                                          (model, persistence, context_mean)):
                diff = prediction[:, :, j]-np.where(valid, truth, 0)
                self.sq[method][j] += np.where(valid, diff**2, 0).sum(axis=0)
                self.abs[method][j] += np.where(valid, np.abs(diff), 0).sum(axis=0)
                self.exact[method][j] += (valid & (diff == 0)).sum(axis=0)

    def summary(self):
        total = int(self.count.sum())
        return {method: dict(mse=float(self.sq[method].sum()/total),
                             mae=float(self.abs[method].sum()/total),
                             exact=float(self.exact[method].sum()/total),
                             observedTargets=total)
                for method in self.methods}


def score_model(model, dataset, index, validation, split, batch_size, limit,
                count, names, bucket, stats, device, seed):
    model.eval()
    scores = Scores(bucket, model.horizon)
    with torch.inference_mode():
        for values in batches(dataset, index, split, validation, batch_size,
                              limit, count, seed, names):
            context = values[:, :model.context]
            future = values[:, model.context:model.context+model.horizon]
            tensor = torch.from_numpy(context).to(device)
            predictions = [head.argmax(-1).cpu().numpy() for head in model(tensor)]
            predictions = np.stack(predictions, -1)
            last, avg = baseline(context, bucket, stats['median'])
            scores.add(future, predictions,
                       np.repeat(last[:, None, :], model.horizon, axis=1),
                       np.repeat(avg[:, None, :], model.horizon, axis=1), bucket)
    if not scores.count.sum():
        raise ValueError('evaluation subset has no observed bucket targets')
    return scores


def write_results(out: Path, scores: Scores, bucket, index: dict):
    summary = scores.summary()
    atomic_json(out/'comparison.json', dict(splitUnit=index['splitUnit'],
        context=index['context'], future=index['future'], methods=summary,
        mseDefinition='mean squared difference of predicted and true bucket indices; finite targets only',
        baselineMissing='training-set median bucket if last context token or whole context is missing'))
    with (out/'feature-mse.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['feature','classes','observed_targets','missing_fraction',
                         'model_mse','persistence_mse','context_mean_mse',
                         'model_exact','persistence_exact','context_mean_exact'])
        for j, (_, name, classes) in enumerate(bucket):
            count = scores.count[j].sum()
            total = scores.total[j].sum()
            writer.writerow([name, classes, int(count), 1-count/total if total else math.nan,
                *[scores.sq[method][j].sum()/count if count else math.nan
                  for method in scores.methods],
                *[scores.exact[method][j].sum()/count if count else math.nan
                  for method in scores.methods]])
    with (out/'horizon-mse.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['horizon_step','method','mse','observed_targets'])
        for step in range(scores.count.shape[1]):
            count = scores.count[:, step].sum()
            for method in scores.methods:
                writer.writerow([step+1, method,
                                 scores.sq[method][:, step].sum()/count if count else math.nan,
                                 int(count)])
    plot_results(out, scores)


def plot_results(out: Path, scores: Scores):
    plt.rcParams.update({'font.size': 9, 'figure.dpi': 150})
    n = len(scores.names)
    order = np.argsort(np.divide(scores.sq['persistence'].sum(axis=1),
                                  scores.count.sum(axis=1),
                                  out=np.zeros(n), where=scores.count.sum(axis=1)>0))
    labels = [scores.names[j].removesuffix('_bucket') for j in order]
    y = np.arange(n)
    height = max(7, .27*n)
    fig, ax = plt.subplots(figsize=(12, height))
    colors = dict(model='#205b91', persistence='#d87931', context_mean='#619f73')
    for offset, method in zip((-.26, 0, .26), scores.methods):
        mse = np.divide(scores.sq[method].sum(axis=1), scores.count.sum(axis=1),
                        out=np.full(n, np.nan), where=scores.count.sum(axis=1)>0)
        ax.barh(y+offset, mse[order], height=.25, label=method.replace('_',' '),
                color=colors[method])
    ax.set_yticks(y, labels)
    ax.set_xlabel('Test MSE of bucket index (lower is better)')
    ax.set_title('Future bucket prediction by feature')
    ax.legend()
    fig.tight_layout()
    fig.savefig(out/'feature-mse.png')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    steps = np.arange(1, scores.count.shape[1]+1)
    for method in scores.methods:
        value = np.divide(scores.sq[method].sum(axis=0), scores.count.sum(axis=0),
                          out=np.full(len(steps), np.nan), where=scores.count.sum(axis=0)>0)
        ax.plot(steps, value, marker='o', label=method.replace('_',' '),
                color=colors[method])
    ax.set(xlabel='Future token step', ylabel='Bucket-index MSE',
           title='Predictability across the future horizon')
    ax.legend()
    fig.tight_layout()
    fig.savefig(out/'horizon-mse.png')
    plt.close(fig)

    base = np.divide(scores.sq['persistence'].sum(axis=1), scores.count.sum(axis=1),
                     out=np.full(n, np.nan), where=scores.count.sum(axis=1)>0)
    learned = np.divide(scores.sq['model'].sum(axis=1), scores.count.sum(axis=1),
                        out=np.full(n, np.nan), where=scores.count.sum(axis=1)>0)
    gain = np.divide(base-learned, base, out=np.full(n, np.nan), where=base>0)
    order = np.argsort(np.nan_to_num(gain, nan=-np.inf))
    fig, ax = plt.subplots(figsize=(11, height))
    ax.barh(np.arange(n), 100*gain[order], color=np.where(gain[order]>=0,
                                                  '#205b91', '#c04b43'))
    ax.axvline(0, color='black', linewidth=.8)
    ax.set_yticks(np.arange(n), [scores.names[j].removesuffix('_bucket') for j in order])
    ax.set(xlabel='Model MSE improvement over persistence (%)',
           title='Which token features are more predictable?')
    fig.tight_layout()
    fig.savefig(out/'feature-improvement.png')
    plt.close(fig)

    matrix = np.divide(scores.sq['model'], scores.count,
                       out=np.full_like(scores.sq['model'], np.nan), where=scores.count>0)
    fig, ax = plt.subplots(figsize=(11, height))
    image = ax.imshow(matrix, aspect='auto', interpolation='nearest', cmap='viridis')
    ax.set_yticks(np.arange(n), [name.removesuffix('_bucket') for name in scores.names])
    ax.set_xticks(np.arange(matrix.shape[1]), np.arange(1, matrix.shape[1]+1))
    ax.set(xlabel='Future token step', title='Model bucket-index MSE by feature and horizon')
    fig.colorbar(image, ax=ax, label='MSE')
    fig.tight_layout()
    fig.savefig(out/'feature-horizon-mse.png')
    plt.close(fig)


def train(args):
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError('output must be outside the input dataset')
    index, names, bucket, direct, context, horizon = schema(dataset)
    validation, cases = validation_runs(dataset, index, args.validation_fraction, args.seed)
    counts = count_windows(dataset, index, validation)
    output.mkdir(parents=True, exist_ok=True)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = torch.device('cuda' if args.device == 'auto' and torch.cuda.is_available()
                          else 'mps' if args.device == 'auto' and torch.backends.mps.is_available()
                          else 'cpu' if args.device == 'auto' else args.device)
    stats = fit_input_stats(dataset, index, validation, bucket, direct,
                            args.batch_size, counts['train'], names)
    atomic_json(output/'run-plan.json', dict(dataset=str(dataset), counts=counts,
        validationCases=sorted(cases), featureNames=names,
        bucketFeatures=[dict(name=name, classes=classes) for _, name, classes in bucket],
        trials=[asdict(trial) for trial in TRIALS if trial.name in args.trials],
        args={key: str(value) if isinstance(value, Path) else value
              for key, value in vars(args).items()}, stats=stats))
    trial_results = []
    for trial in (item for item in TRIALS if item.name in args.trials):
        model = UnumBuckets(bucket, direct, context, horizon, trial, stats).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=trial.learning_rate,
                                       weight_decay=.01)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min',
                                                                factor=.5, patience=2)
        best = math.inf
        best_epoch = 0
        history = []
        checkpoint = output/f'{trial.name}.pt'
        for epoch in range(1, args.epochs+1):
            model.train()
            train_loss = 0.
            steps = 0
            for values in batches(dataset, index, 'train', validation,
                                  args.batch_size, args.train_windows_per_epoch,
                                  counts['train'], args.seed+epoch, names):
                tensor = torch.from_numpy(values).to(device)
                optimizer.zero_grad(set_to_none=True)
                logits = model(tensor[:, :context])
                loss = objective(logits, tensor[:, context:context+horizon],
                                 bucket, trial.ordinal_weight)
                if loss is None:
                    continue
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1.)
                optimizer.step()
                train_loss += float(loss.detach())
                steps += 1
            if not steps:
                raise ValueError('no observed training targets')
            validation_scores = score_model(model, dataset, index, validation,
                'validation', args.batch_size, args.validation_windows,
                counts['validation'], names, bucket, stats, device, args.seed)
            mse = validation_scores.summary()['model']['mse']
            scheduler.step(mse)
            history.append(dict(epoch=epoch, trainLoss=train_loss/steps,
                                validationMSE=mse, learningRate=optimizer.param_groups[0]['lr']))
            print(trial.name, 'epoch', epoch, 'validation bucket MSE', round(mse, 4), flush=True)
            if mse < best-args.min_improvement:
                best, best_epoch = mse, epoch
                torch.save(dict(state=model.state_dict(), trial=asdict(trial),
                                stats=stats, names=names, context=context,
                                horizon=horizon), checkpoint)
            if epoch-best_epoch >= args.patience:
                break
        trial_results.append(dict(name=trial.name, bestValidationMSE=best,
                                  bestEpoch=best_epoch, history=history,
                                  checkpoint=str(checkpoint)))
        atomic_json(output/'tuning.json', trial_results)
    selected = min(trial_results, key=lambda entry: entry['bestValidationMSE'])
    trial = next(item for item in TRIALS if item.name == selected['name'])
    model = UnumBuckets(bucket, direct, context, horizon, trial, stats).to(device)
    checkpoint = torch.load(selected['checkpoint'], map_location=device, weights_only=True)
    model.load_state_dict(checkpoint['state'])
    scores = score_model(model, dataset, index, validation, 'test',
        args.batch_size, args.test_windows, counts['test'], names, bucket, stats, device, args.seed)
    write_results(output, scores, bucket, index)
    atomic_json(output/'selected-model.json', selected)
    print(json.dumps(scores.summary(), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True,
                        help='finalized windows-stratified-c10-p10-s20 directory')
    parser.add_argument('--output', type=Path, required=True,
                        help='new local directory for checkpoints, scores, and plots')
    parser.add_argument('--epochs', type=int, default=25)
    parser.add_argument('--patience', type=int, default=5)
    parser.add_argument('--min-improvement', type=float, default=.001)
    parser.add_argument('--batch-size', type=int, default=512)
    parser.add_argument('--train-windows-per-epoch', type=int, default=500000)
    parser.add_argument('--validation-windows', type=int, default=100000)
    parser.add_argument('--test-windows', type=int, default=None)
    parser.add_argument('--trials', nargs='+', choices=tuple(t.name for t in TRIALS),
                        default=[t.name for t in TRIALS])
    parser.add_argument('--validation-fraction', type=float, default=.1)
    parser.add_argument('--seed', type=int, default=2026)
    parser.add_argument('--device', choices=('auto', 'cpu', 'cuda', 'mps'), default='auto')
    args = parser.parse_args()
    if (args.epochs < 1 or args.patience < 1 or args.batch_size < 1 or
            args.train_windows_per_epoch < 1 or args.validation_windows < 1 or
            (args.test_windows is not None and args.test_windows < 1) or
            not 0 < args.validation_fraction < 1):
        parser.error('epochs, patience, batch size and limits must be positive; validation fraction must be in (0,1)')
    train(args)


if __name__ == '__main__':
    main()
