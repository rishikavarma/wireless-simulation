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
    # Width 64 cannot carry ~200 categorical features through one residual stream.
    Trial('w256', 256, 8, 3, 2, 512, .1, 5e-4, .1),
)


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def schema(dataset: Path, selected_features: set[str] | None = None):
    index = json.loads((dataset/'dataset-index.json').read_text())
    if index.get('format') != 'sharded-npz-v1':
        raise ValueError('use a finalized sharded dataset')
    if index.get('splitUnit') not in ('sample', 'stratified-run', 'profile'):
        raise ValueError('use a sample, stratified-run, or profile-held-out dataset')
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
    if selected_features is not None:
        unknown = selected_features - {name for _, name, _ in bucket}
        if unknown:
            raise ValueError('selected bucket features are absent: ' + ', '.join(sorted(unknown)))
        bucket = [row for row in bucket if row[1] in selected_features]
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
    train_runs = set(index['splits']['train']['runs'])
    test_runs = set(index['splits']['test']['runs'])
    if index.get('splitUnit') == 'sample':
        validation = set(index['splits']['validation']['runs'])
        if not train_runs or not validation or not test_runs:
            raise ValueError('sample train/validation/test must contain runs')
        if index.get('requireSameRuns') and (train_runs != validation or train_runs != test_runs):
            raise ValueError('sample train/validation/test do not use the same runs')
        return validation, []
    source = Path(index['sourceDataset'])
    if not source.is_absolute():
        source = (dataset/source).resolve()
    if not (source/'manifest.json').is_file():
        source = dataset.parent
    if not (source/'manifest.json').is_file():
        raise ValueError('source manifest is missing beside the dataset')
    manifest = json.loads((source/'manifest.json').read_text())
    by_run = {str(row['prefix']): str(row['case']) for row in manifest['runs']}
    if 'validation' in index['splits']:
        validation = set(index['splits']['validation']['runs'])
        if index.get('splitUnit') != 'sample' and (
                train_runs & test_runs or train_runs & validation or test_runs & validation):
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
    return input_stats(batches(dataset, index, 'train', validation, batch_size,
                               None, count, 0, names),
                       index['context'], bucket, direct)


def input_stats(window_batches, context_length, bucket, direct):
    hist = [np.zeros(classes, dtype=np.int64) for _, _, classes in bucket]
    sums = np.zeros(len(direct), dtype=np.float64)
    squares = np.zeros(len(direct), dtype=np.float64)
    seen = np.zeros(len(direct), dtype=np.int64)
    for values in window_batches:
        context = values[:, :context_length]
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
    """Causal context encoder, future-query decoder, one head per feature.

    Per-feature embeddings and heads are stored as one fused table/matrix; this
    is mathematically the same as separate modules but much faster.
    With ``delta=True`` each head scores the *offset* from the last observed
    context bucket and the offsets are mapped back to absolute buckets, so
    "stay where you are" is a single shared logit instead of a per-value mapping.
    The returned logits are always over absolute bucket IDs.
    """
    def __init__(self, bucket, direct, context, horizon, trial: Trial, stats,
                 delta: bool = True):
        super().__init__()
        self.bucket = bucket
        self.direct = direct
        self.context = context
        self.horizon = horizon
        self.delta = delta
        classes = [c for _, _, c in bucket]
        self.register_buffer('direct_mean', torch.tensor(stats['mean'], dtype=torch.float32))
        self.register_buffer('direct_scale', torch.tensor(stats['scale'], dtype=torch.float32))
        self.register_buffer('positions', torch.tensor([p for p, _, _ in bucket]))
        self.register_buffer('classes', torch.tensor(classes))
        self.register_buffer('offsets', torch.tensor(
            np.concatenate([[0], np.cumsum(np.asarray(classes)+1)[:-1]])))
        self.register_buffer('median', torch.tensor(stats['median'], dtype=torch.long))
        self.embedding = nn.EmbeddingBag(int(sum(classes)+len(classes)), trial.width, mode='sum')
        self.direct_projection = nn.Linear(2*len(direct), trial.width) if direct else None
        self.input_norm = nn.LayerNorm(trial.width)
        self.context_position = nn.Parameter(torch.zeros(context, trial.width))
        self.future_position = nn.Parameter(torch.zeros(horizon, trial.width))
        encoder = nn.TransformerEncoderLayer(trial.width, trial.heads,
            trial.feedforward, trial.dropout, batch_first=True, norm_first=True)
        decoder = nn.TransformerDecoderLayer(trial.width, trial.heads,
            trial.feedforward, trial.dropout, batch_first=True, norm_first=True)
        self.encoder = nn.TransformerEncoder(encoder, trial.encoder_layers,
                                             enable_nested_tensor=False)
        self.decoder = nn.TransformerDecoder(decoder, trial.decoder_layers)
        self.sizes = [(2*c-1 if delta else c) for c in classes]
        self.heads = nn.Linear(trial.width, sum(self.sizes))
        nn.init.normal_(self.context_position, std=.02)
        nn.init.normal_(self.future_position, std=.02)

    def forward(self, context_tokens):
        batch = context_tokens.shape[0]
        planes = context_tokens[:, :, self.positions]
        missing = ~torch.isfinite(planes)
        ids = torch.where(missing, self.classes.to(planes.dtype), planes).long()
        ids = torch.minimum(ids.clamp_min(0), self.classes)
        flat = (ids + self.offsets).reshape(batch*self.context, -1)
        x = self.embedding(flat).reshape(batch, self.context, -1)
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
        pieces = self.heads(decoded).split(self.sizes, dim=-1)
        if not self.delta:
            return list(pieces)
        last = planes[:, -1]
        last = torch.where(torch.isfinite(last), last, self.median.to(last.dtype)).long()
        out = []
        for j, piece in enumerate(pieces):
            c = int(self.classes[j])
            shift = torch.arange(c, device=piece.device)[None, :] - last[:, j, None].clamp(0, c-1) + (c-1)
            out.append(piece.gather(2, shift[:, None, :].expand(-1, self.horizon, -1)))
        return out


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
    def __init__(self, features, horizon, cross_entropy=False, baseline_ce=False):
        # cross_entropy: also score the model's CE; baseline_ce: score baseline CE too.
        self.names = [name for _, name, _ in features]
        self.methods = ('model', 'persistence', 'context_mean')
        self.sq = {method: np.zeros((len(features), horizon), dtype=np.float64)
                   for method in self.methods}
        self.abs = {method: np.zeros((len(features), horizon), dtype=np.float64)
                    for method in self.methods}
        self.exact = {method: np.zeros((len(features), horizon), dtype=np.int64)
                      for method in self.methods}
        self.within_one = {method: np.zeros((len(features), horizon), dtype=np.int64)
                           for method in self.methods}
        self.count = np.zeros((len(features), horizon), dtype=np.int64)
        self.total = np.zeros((len(features), horizon), dtype=np.int64)
        # squared error of the model's expected bucket (the MSE-optimal point estimate)
        self.sq_expected = np.zeros((len(features), horizon), dtype=np.float64)
        self.baseline_ce = baseline_ce
        self.ce = ({method: np.zeros((len(features), horizon), dtype=np.float64)
                    for method in self.methods} if cross_entropy else None)

    def add(self, values, model, persistence, context_mean, bucket, expected=None):
        for j, (position, _, _) in enumerate(bucket):
            truth = values[:, :, position]
            valid = np.isfinite(truth)
            self.count[j] += valid.sum(axis=0)
            self.total[j] += len(truth)
            if expected is not None:
                diff = expected[:, :, j]-np.where(valid, truth, 0)
                self.sq_expected[j] += np.where(valid, diff**2, 0).sum(axis=0)
            for method, prediction in zip(self.methods,
                                          (model, persistence, context_mean)):
                diff = prediction[:, :, j]-np.where(valid, truth, 0)
                self.sq[method][j] += np.where(valid, diff**2, 0).sum(axis=0)
                self.abs[method][j] += np.where(valid, np.abs(diff), 0).sum(axis=0)
                self.exact[method][j] += (valid & (diff == 0)).sum(axis=0)
                self.within_one[method][j] += (valid & (np.abs(diff) <= 1)).sum(axis=0)

    def per_feature(self, table):
        count = self.count.sum(axis=1)
        return np.divide(table.sum(axis=1), count, out=np.full(len(count), np.nan),
                         where=count > 0)

    def skill(self, model_value, base_value, floor=1e-9):
        """1 - model/baseline per feature; NaN where the baseline is already ~perfect."""
        return np.where(base_value > floor, 1-model_value/np.maximum(base_value, floor), np.nan)

    @staticmethod
    def skill_summary(skill):
        ok = skill[np.isfinite(skill)]
        if not len(ok):
            return None
        return dict(features=int(len(ok)), median=float(np.median(ok)),
                    meanClippedAtMinus1=float(np.clip(ok, -1, 1).mean()),
                    fractionBeatingBaseline=float((ok > 0).mean()))

    def summary(self):
        total = int(self.count.sum())
        result = {}
        for method in self.methods:
            entry = dict(mse=float(self.sq[method].sum()/total),
                         mae=float(self.abs[method].sum()/total),
                         meanBucketIndexError=float(self.abs[method].sum()/total),
                         withinOneBucket=float(self.within_one[method].sum()/total),
                         exact=float(self.exact[method].sum()/total),
                         observedTargets=total)
            if self.ce is not None and (method == 'model' or self.baseline_ce):
                entry['crossEntropy'] = float(self.ce[method].sum()/total)
            result[method] = entry
        result['model']['expectedValueMse'] = float(self.sq_expected.sum()/total)
        return result

    def skill_report(self):
        """Per-feature skill relative to persistence (point-5 metric); macro-averaged."""
        report = dict(
            bucketIndexError=self.skill_summary(self.skill(self.per_feature(self.abs['model']),
                                                        self.per_feature(self.abs['persistence']))),
            mseArgmax=self.skill_summary(self.skill(self.per_feature(self.sq['model']),
                                                    self.per_feature(self.sq['persistence']))),
            mseExpected=self.skill_summary(self.skill(self.per_feature(self.sq_expected),
                                                      self.per_feature(self.sq['persistence']))))
        if self.ce is not None and self.baseline_ce:
            report['crossEntropyVsPersistenceTable'] = self.skill_summary(self.skill(
                self.per_feature(self.ce['model']), self.per_feature(self.ce['persistence'])))
            report['crossEntropyVsContextMeanTable'] = self.skill_summary(self.skill(
                self.per_feature(self.ce['model']), self.per_feature(self.ce['context_mean'])))
        return report


def fit_transition_tables(dataset, index, validation, bucket, stats, count, names,
                          batch_size, limit=100000, seed=2036, alpha=.5):
    """Empirical P(true bucket | point prediction, horizon) per feature, from training windows.

    This gives the point baselines a fair, calibrated probability: a Gaussian around
    the prediction cannot represent "stays put with probability 0.99", which
    persistence does on most features. Add-alpha smoothing; training data only.
    """
    horizon = index['future']
    counts = {method: [np.zeros((horizon, c, c), dtype=np.float64) for _, _, c in bucket]
              for method in ('persistence', 'context_mean')}
    steps = np.arange(horizon)[None, :]
    for values in batches(dataset, index, 'train', validation, batch_size,
                          limit, count, seed, names):
        context = values[:, :index['context']]
        future = values[:, index['context']:index['context']+horizon]
        last, mean = baseline(context, bucket, stats['median'])
        for j, (position, _, classes) in enumerate(bucket):
            truth = future[:, :, position]
            valid = np.isfinite(truth)
            if not valid.any():
                continue
            actual = np.where(valid, truth, 0).astype(np.int64)
            for method, point in (('persistence', last), ('context_mean', mean)):
                flat = (steps*classes*classes + point[:, None, j].astype(np.int64)*classes + actual)
                counts[method][j] += np.bincount(flat[valid], minlength=horizon*classes*classes
                                                 ).reshape(horizon, classes, classes)
    tables = {}
    for method, per_feature in counts.items():
        tables[method] = []
        for table in per_feature:
            smooth = table + alpha
            tables[method].append(-np.log(smooth/smooth.sum(axis=-1, keepdims=True)))
    return tables


def score_model(model, dataset, index, validation, split, batch_size, limit,
                count, names, bucket, stats, device, seed, baseline_tables=None,
                windows=None):
    """Score on a split; ``windows`` (array) overrides the dataset reader (sanity checks)."""
    model.eval()
    scores = Scores(bucket, model.horizon, cross_entropy=True,
                    baseline_ce=baseline_tables is not None)
    indices = torch.arange(max(c for _, _, c in bucket), device=device)
    source = (windows[i:i+batch_size] for i in range(0, len(windows), batch_size)) \
        if windows is not None else batches(dataset, index, split, validation,
                                            batch_size, limit, count, seed, names)
    with torch.inference_mode():
        for values in source:
            context = values[:, :model.context]
            future = values[:, model.context:model.context+model.horizon]
            tensor = torch.from_numpy(np.ascontiguousarray(context)).to(device)
            logits = model(tensor)
            predictions = np.stack([head.argmax(-1).cpu().numpy() for head in logits], -1)
            expected = np.stack([(head.softmax(-1)*indices[:head.shape[-1]]).sum(-1).cpu().numpy()
                                 for head in logits], -1)
            last, avg = baseline(context, bucket, stats['median'])
            scores.add(future, predictions,
                       np.repeat(last[:, None, :], model.horizon, axis=1),
                       np.repeat(avg[:, None, :], model.horizon, axis=1), bucket,
                       expected=expected)
            steps = np.arange(model.horizon)[None, :]
            for j, (position, _, classes) in enumerate(bucket):
                truth = future[:, :, position]
                valid = np.isfinite(truth)
                actual = np.where(valid, truth, 0).astype(np.int64)
                target = torch.from_numpy(actual).to(device)
                model_nll = F.cross_entropy(logits[j].reshape(-1, classes),
                                            target.reshape(-1), reduction='none')
                model_nll = model_nll.reshape(actual.shape).cpu().numpy()
                scores.ce['model'][j] += np.where(valid, model_nll, 0).sum(axis=0)
                if baseline_tables is not None:
                    for method, point in (('persistence', last), ('context_mean', avg)):
                        table = baseline_tables[method][j]
                        baseline_nll = table[steps, point[:, None, j].astype(np.int64), actual]
                        scores.ce[method][j] += np.where(valid, baseline_nll, 0).sum(axis=0)
    if not scores.count.sum():
        raise ValueError('evaluation subset has no observed bucket targets')
    return scores


def write_results(out: Path, scores: Scores, bucket, index: dict,
                  history: list[dict] | None = None):
    summary = scores.summary()
    atomic_json(out/'comparison.json', dict(splitUnit=index['splitUnit'],
        context=index['context'], future=index['future'], methods=summary,
        perFeatureSkill=scores.skill_report(),
        primaryMetric='meanBucketIndexError',
        bucketIndexErrorDefinition='mean absolute difference between predicted (argmax) and true bucket indices; finite targets only; lower is better. withinOneBucket is the fraction with absolute bucket-index error <= 1; higher is better',
        mseDefinition='mean squared difference of predicted (argmax) and true bucket indices; finite targets only. expectedValueMse uses the expected bucket of the predicted distribution',
        crossEntropyDefinition='mean negative log probability of the true bucket. Baselines use empirical P(true bucket | baseline point prediction, horizon) tables fitted on training windows (add-0.5 smoothing)',
        skillDefinition='per-feature 1 - model/baseline (baseline = persistence unless stated); features whose baseline score is ~0 are excluded; summarized by median, mean clipped at -1, and fraction of features beating the baseline',
        baselineMissing='training-set median bucket if last context token or whole context is missing'))
    methods = scores.methods
    nan = math.nan

    def per(table, j, count):
        return table[j].sum()/count if count else nan
    skill_mse = scores.skill(scores.per_feature(scores.sq['model']),
                             scores.per_feature(scores.sq['persistence']))
    skill_bucket = scores.skill(scores.per_feature(scores.abs['model']),
                                scores.per_feature(scores.abs['persistence']))
    skill_exp = scores.skill(scores.per_feature(scores.sq_expected),
                             scores.per_feature(scores.sq['persistence']))
    skill_ce = (scores.skill(scores.per_feature(scores.ce['model']),
                             scores.per_feature(scores.ce['persistence']))
                if scores.ce is not None and scores.baseline_ce else np.full(len(bucket), nan))
    with (out/'feature-mse.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['feature','classes','observed_targets','missing_fraction',
                         'model_bucket_index_error','persistence_bucket_index_error','context_mean_bucket_index_error',
                         'model_within_one_bucket','persistence_within_one_bucket','context_mean_within_one_bucket',
                         'skill_bucket_index_error',
                         'model_mse','persistence_mse','context_mean_mse',
                         'model_exact','persistence_exact','context_mean_exact',
                         'model_cross_entropy','persistence_cross_entropy','context_mean_cross_entropy',
                         'model_expected_value_mse','skill_mse_argmax','skill_mse_expected',
                         'skill_cross_entropy'])
        for j, (_, name, classes) in enumerate(bucket):
            count = scores.count[j].sum()
            total = scores.total[j].sum()
            writer.writerow([name, classes, int(count), 1-count/total if total else nan,
                *[per(scores.abs[method], j, count) for method in methods],
                *[per(scores.within_one[method], j, count) for method in methods],
                skill_bucket[j],
                *[per(scores.sq[method], j, count) for method in methods],
                *[per(scores.exact[method], j, count) for method in methods],
                *[per(scores.ce[method], j, count) if scores.ce is not None
                  and (method == 'model' or scores.baseline_ce) else nan for method in methods],
                per(scores.sq_expected, j, count), skill_mse[j], skill_exp[j], skill_ce[j]])
    with (out/'horizon-mse.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['horizon_step','method','mse','observed_targets'])
        for step in range(scores.count.shape[1]):
            count = scores.count[:, step].sum()
            for method in scores.methods:
                writer.writerow([step+1, method,
                                 scores.sq[method][:, step].sum()/count if count else math.nan,
                                 int(count)])
    with (out/'horizon-bucket-error.csv').open('w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['horizon_step','method','mean_bucket_index_error',
                         'within_one_bucket','observed_targets'])
        for step in range(scores.count.shape[1]):
            count = scores.count[:, step].sum()
            for method in scores.methods:
                writer.writerow([step+1, method,
                                 scores.abs[method][:, step].sum()/count if count else math.nan,
                                 scores.within_one[method][:, step].sum()/count if count else math.nan,
                                 int(count)])
    plot_results(out, scores, history=history)


def plot_training_history(out: Path, history: list[dict]) -> None:
    if not history:
        return
    epochs = [row['epoch'] for row in history]
    train_loss = [row['trainLoss'] for row in history]
    fig, ax = plt.subplots(figsize=(8, 4.8))
    ax.plot(epochs, train_loss, color='#205b91', marker='o', ms=3, label='train loss')
    if 'validationBucketIndexError' in history[0]:
        ax2 = ax.twinx()
        ax2.plot(epochs, [row['validationBucketIndexError'] for row in history],
                 color='#d87931', marker='s', ms=3, label='val bucket-index error')
        ax2.set_ylabel('Validation mean absolute bucket-index error')
        lines, labels = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines + lines2, labels + labels2, loc='upper right')
    else:
        ax.legend(loc='upper right')
    ax.set(xlabel='Epoch', ylabel='Training loss',
           title='Training loss over epochs')
    ax.grid(alpha=.2)
    fig.tight_layout()
    fig.savefig(out / 'training-loss.png')
    plt.close(fig)


def plot_results(out: Path, scores: Scores, history: list[dict] | None = None):
    plt.rcParams.update({'font.size': 9, 'figure.dpi': 150})
    if history:
        plot_training_history(out, history)
    n = len(scores.names)
    order = np.argsort(np.divide(scores.abs['persistence'].sum(axis=1),
                                  scores.count.sum(axis=1),
                                  out=np.zeros(n), where=scores.count.sum(axis=1)>0))
    labels = [scores.names[j].removesuffix('_bucket') for j in order]
    y = np.arange(n)
    height = max(7, .27*n)
    fig, ax = plt.subplots(figsize=(12, height))
    colors = dict(model='#205b91', persistence='#d87931', context_mean='#619f73')
    for offset, method in zip((-.26, 0, .26), scores.methods):
        error = np.divide(scores.abs[method].sum(axis=1), scores.count.sum(axis=1),
                        out=np.full(n, np.nan), where=scores.count.sum(axis=1)>0)
        ax.barh(y+offset, error[order], height=.25, label=method.replace('_',' '),
                color=colors[method])
    ax.set_yticks(y, labels)
    ax.set_xlabel('Mean absolute bucket-index error (lower is better)')
    ax.set_title('Future bucket prediction by feature')
    ax.legend()
    fig.tight_layout()
    fig.savefig(out/'feature-bucket-error.png')
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 5))
    steps = np.arange(1, scores.count.shape[1]+1)
    for method in scores.methods:
        value = np.divide(scores.abs[method].sum(axis=0), scores.count.sum(axis=0),
                          out=np.full(len(steps), np.nan), where=scores.count.sum(axis=0)>0)
        ax.plot(steps, value, marker='o', label=method.replace('_',' '),
                color=colors[method])
    ax.set(xlabel='Future token step', ylabel='Mean absolute bucket-index error',
           title='Predictability across the future horizon')
    ax.legend()
    fig.tight_layout()
    fig.savefig(out/'horizon-bucket-error.png')
    plt.close(fig)

    base = np.divide(scores.abs['persistence'].sum(axis=1), scores.count.sum(axis=1),
                     out=np.full(n, np.nan), where=scores.count.sum(axis=1)>0)
    learned = np.divide(scores.abs['model'].sum(axis=1), scores.count.sum(axis=1),
                        out=np.full(n, np.nan), where=scores.count.sum(axis=1)>0)
    gain = np.divide(base-learned, base, out=np.full(n, np.nan), where=base>0)
    order = np.argsort(np.nan_to_num(gain, nan=-np.inf))
    fig, ax = plt.subplots(figsize=(11, height))
    ax.barh(np.arange(n), 100*gain[order], color=np.where(gain[order]>=0,
                                                  '#205b91', '#c04b43'))
    ax.axvline(0, color='black', linewidth=.8)
    ax.set_yticks(np.arange(n), [scores.names[j].removesuffix('_bucket') for j in order])
    ax.set(xlabel='Model bucket-index error improvement over persistence (%)',
           title='Which token features are more predictable?')
    fig.tight_layout()
    fig.savefig(out/'feature-improvement.png')
    plt.close(fig)

    matrix = np.divide(scores.abs['model'], scores.count,
                       out=np.full_like(scores.abs['model'], np.nan), where=scores.count>0)
    fig, ax = plt.subplots(figsize=(11, height))
    image = ax.imshow(matrix, aspect='auto', interpolation='nearest', cmap='viridis')
    ax.set_yticks(np.arange(n), [name.removesuffix('_bucket') for name in scores.names])
    ax.set_xticks(np.arange(matrix.shape[1]), np.arange(1, matrix.shape[1]+1))
    ax.set(xlabel='Future token step', title='Model bucket-index error by feature and horizon')
    fig.colorbar(image, ax=ax, label='Mean absolute bucket-index error')
    fig.tight_layout()
    fig.savefig(out/'feature-horizon-bucket-error.png')
    plt.close(fig)


def make_model(architecture, bucket, direct, context, horizon, trial, stats, target):
    if architecture == 'transformer':
        return UnumBuckets(bucket, direct, context, horizon, trial, stats,
                           delta=target == 'delta')
    from unum_baselines import LSTMBuckets, MLPBuckets
    model_type = {'mlp': MLPBuckets, 'lstm': LSTMBuckets}[architecture]
    return model_type(bucket, direct, context, horizon, trial, stats,
                      delta=target == 'delta')


def train(args):
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    if output == dataset or output.is_relative_to(dataset):
        raise ValueError('output must be outside the input dataset')
    if args.architecture != 'transformer' and args.target != 'absolute':
        raise ValueError('MLP and LSTM baselines predict absolute bucket classes')
    selected_features = None
    if args.bucket_features is not None:
        selected = json.loads(args.bucket_features.read_text())
        if isinstance(selected, dict):
            selected = selected['selectedFeatures']
        if not isinstance(selected, list) or not selected or len(selected) != len(set(selected)):
            raise ValueError('bucket feature selection must be a nonempty unique name list')
        selected_features = set(selected)
    index, names, bucket, direct, context, horizon = schema(dataset, selected_features)
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
    baseline_tables = fit_transition_tables(dataset, index, validation, bucket, stats,
                                            counts['train'], names, args.batch_size)
    atomic_json(output/'baseline-calibration.json', dict(
        method='empirical P(true bucket | baseline point prediction, horizon) from training windows, add-0.5 smoothing',
        sampleLimit=100000, seed=2036, features=[name for _, name, _ in bucket]))
    selected_trials = [trial for trial in TRIALS if trial.name in args.trials]
    atomic_json(output/'run-plan.json', dict(dataset=str(dataset), counts=counts,
        validationCases=sorted(cases), featureNames=names,
        bucketFeatures=[dict(name=name, classes=classes) for _, name, classes in bucket],
        trials=[asdict(trial) for trial in selected_trials],
        architecture=args.architecture,
        parameterCounts={trial.name: sum(p.numel() for p in make_model(
            args.architecture, bucket, direct, context, horizon, trial, stats,
            args.target).parameters()) for trial in selected_trials},
        args={key: str(value) if isinstance(value, Path) else value
              for key, value in vars(args).items()}, stats=stats,
        baselineCalibration='baseline-calibration.json'))
    trial_results = []
    for trial in (item for item in TRIALS if item.name in args.trials):
        model = make_model(args.architecture, bucket, direct, context, horizon,
                           trial, stats, args.target).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=trial.learning_rate,
                                       weight_decay=.01)
        steps_per_epoch = math.ceil(min(args.train_windows_per_epoch, counts['train'])
                                    / args.batch_size)
        total_steps = max(1, args.epochs*steps_per_epoch)
        if args.schedule == 'cosine':
            def factor(step):
                if step < args.warmup_steps:
                    return (step+1)/args.warmup_steps
                progress = min(1., (step-args.warmup_steps)/max(1, total_steps-args.warmup_steps))
                return .02+.98*.5*(1+math.cos(math.pi*progress))
            scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, factor)
        else:
            scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min',
                                                                    factor=.5, patience=2)
        best = math.inf
        best_epoch = 0
        history = []
        checkpoint = output/f'{trial.name}.pt'
        latest_state = output/f'{trial.name}-latest.pt'
        start_epoch = 1
        if args.resume:
            if not latest_state.is_file():
                raise ValueError(f'no epoch checkpoint to resume: {latest_state}')
            saved = torch.load(latest_state, map_location='cpu', weights_only=False)
            if (saved['trial'] != trial.name or saved['target'] != args.target or
                    saved['featureNames'] != names or
                    saved['dataset'] != str(dataset)):
                raise ValueError('resume checkpoint does not match this training run')
            model.load_state_dict(saved['model'])
            optimizer.load_state_dict(saved['optimizer'])
            scheduler.load_state_dict(saved['scheduler'])
            torch.set_rng_state(saved['torchRng'])
            if torch.cuda.is_available() and saved.get('cudaRng') is not None:
                torch.cuda.set_rng_state_all(saved['cudaRng'])
            best = saved['best']
            best_epoch = saved['bestEpoch']
            history = saved['history']
            start_epoch = saved['epoch'] + 1
            if checkpoint.is_file():
                best_saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
                if best_saved.get('epoch', 0) > saved['epoch']:
                    best = best_saved['validationBucketIndexError']
                    best_epoch = best_saved['epoch']
            print(trial.name, 'resuming after epoch', saved['epoch'], flush=True)
        for epoch in range(start_epoch, args.epochs+1):
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
                if args.schedule == 'cosine':
                    scheduler.step()
                if args.log_every and steps % args.log_every == 0:
                    print(trial.name, 'epoch', epoch, 'step', steps, 'loss',
                          round(float(loss.detach()), 4), flush=True)
                train_loss += float(loss.detach())
                steps += 1
            if not steps:
                raise ValueError('no observed training targets')
            validation_scores = score_model(model, dataset, index, validation,
                'validation', args.batch_size, args.validation_windows,
                counts['validation'], names, bucket, stats, device, args.seed)
            summary = validation_scores.summary()['model']
            criterion = summary['meanBucketIndexError']
            if args.schedule != 'cosine':
                scheduler.step(criterion)
            history.append(dict(epoch=epoch, trainLoss=train_loss/steps,
                                validationBucketIndexError=criterion,
                                validationWithinOneBucket=summary['withinOneBucket'],
                                validationCE=summary['crossEntropy'], validationMSE=summary['mse'],
                                validationExpectedMSE=summary['expectedValueMse'],
                                validationExact=summary['exact'],
                                learningRate=optimizer.param_groups[0]['lr']))
            print(trial.name, 'epoch', epoch, 'validation bucket error', round(criterion, 4),
                  'within one bucket', round(summary['withinOneBucket'], 4),
                  'CE', round(summary['crossEntropy'], 4),
                  flush=True)
            if criterion < best-args.min_improvement:
                best, best_epoch = criterion, epoch
                best_temp = checkpoint.with_suffix('.pt.tmp')
                torch.save(dict(state=model.state_dict(), trial=asdict(trial), target=args.target,
                                architecture=args.architecture, stats=stats,
                                names=names, context=context, horizon=horizon,
                                epoch=epoch, validationBucketIndexError=criterion), best_temp)
                best_temp.replace(checkpoint)
            latest_temp = latest_state.with_suffix('.pt.tmp')
            torch.save(dict(epoch=epoch, trial=trial.name, target=args.target,
                            dataset=str(dataset), featureNames=names,
                            model=model.state_dict(), optimizer=optimizer.state_dict(),
                            scheduler=scheduler.state_dict(), torchRng=torch.get_rng_state(),
                            cudaRng=(torch.cuda.get_rng_state_all()
                                     if torch.cuda.is_available() else None),
                            best=best, bestEpoch=best_epoch, history=history), latest_temp)
            latest_temp.replace(latest_state)
            if epoch-best_epoch >= args.patience:
                break
        trial_results.append(dict(name=trial.name, bestValidationBucketIndexError=best,
                                  bestEpoch=best_epoch, history=history,
                                  checkpoint=str(checkpoint)))
        atomic_json(output/'tuning.json', trial_results)
    selected = min(trial_results, key=lambda entry: entry['bestValidationBucketIndexError'])
    trial = next(item for item in TRIALS if item.name == selected['name'])
    model = make_model(args.architecture, bucket, direct, context, horizon,
                       trial, stats, args.target).to(device)
    checkpoint = torch.load(selected['checkpoint'], map_location=device, weights_only=True)
    model.load_state_dict(checkpoint['state'])
    scores = score_model(model, dataset, index, validation, 'test',
        args.batch_size, args.test_windows, counts['test'], names, bucket, stats,
        device, args.seed, baseline_tables)
    write_results(output, scores, bucket, index, history=selected.get('history'))
    atomic_json(output/'selected-model.json', selected)
    print(json.dumps(dict(methods=scores.summary(), perFeatureSkill=scores.skill_report()), indent=2))


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
                        default=['w256'])
    parser.add_argument('--bucket-features', type=Path, default=None,
                        help='JSON list or selectedFeatures object of bucket names to predict')
    parser.add_argument('--target', choices=('delta', 'absolute'), default='delta',
                        help='delta: heads score the offset from the last observed bucket')
    parser.add_argument('--resume', action='store_true',
                        help='continue from the last completed epoch in the output directory')
    parser.add_argument('--architecture', choices=('transformer', 'mlp', 'lstm'),
                        default='transformer')
    parser.add_argument('--schedule', choices=('cosine', 'plateau'), default='cosine')
    parser.add_argument('--warmup-steps', type=int, default=500)
    parser.add_argument('--log-every', type=int, default=0)
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
