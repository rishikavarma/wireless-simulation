"""Train and compare bucket-only joint forecasts for all 261 token features."""
import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'results/short-stream-tcp-rtt'
WINDOWS = SOURCE / 'windows-profile-c10-p10-s20'
OUT = SOURCE / 'all-feature-models'
SEED = 20261027
CLASSES = 51


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2) + '\n')


def prepare():
    OUT.mkdir(exist_ok=True)
    index = json.loads((WINDOWS/'dataset-index.json').read_text())
    manifest = json.loads((SOURCE/'manifest.json').read_text())
    tokens = json.loads((SOURCE/'token-runs-index.json').read_text())
    names = index['source_feature_names']
    assert len(names) == 261 and names == tokens['sourceFeatures']
    cases=np.array(sorted(index['trainCases']))
    np.random.default_rng(SEED).shuffle(cases)
    sets = dict(train=set(cases[29:]),validation=set(cases[:29]),test=set(index['testCases']))
    assert not (sets['train'] & sets['validation'] or sets['train'] & sets['test'] or sets['validation'] & sets['test'])
    cases_by_run = {r['prefix']:r['case'] for r in manifest['runs']}
    arrays, cases = {s:[] for s in sets}, {s:[] for s in sets}
    # Source run archives contain the continuous values underlying the generated
    # windows, so the new boundaries can be fit on fitting profiles only.
    for split in ('train','test'):
        for run_id in index['splits'][split]['runs']:
            case = cases_by_run[run_id]
            subset = next(s for s in sets if case in sets[s])
            with np.load(SOURCE/tokens['runs'][run_id]['path']) as saved:
                assert str(saved['run_id']) == run_id
                values = saved['continuous'].astype(np.float32)
            starts = np.arange(0,len(values)-19,20)
            arrays[subset].append(np.stack([values[i:i+20] for i in starts]))
            cases[subset].extend([case]*len(starts))
    for s in arrays:
        arrays[s] = np.concatenate(arrays[s])
        cases[s] = np.array(cases[s])
    train_values = arrays['train'].reshape(-1,261)
    edges=[]
    for j in range(261):
        finite = train_values[np.isfinite(train_values[:,j]),j]
        if not len(finite): e=np.empty(0,dtype=np.float32)
        else: e=np.unique(np.quantile(finite,np.arange(1,51)/51).astype(np.float32))
        edges.append(e)
    for s,values in arrays.items():
        bins=np.full(values.shape,-1,dtype=np.int16)
        for j,e in enumerate(edges):
            valid=np.isfinite(values[...,j])
            bins[...,j][valid]=np.searchsorted(e,values[...,j][valid],side='right')
        np.savez_compressed(OUT/f'{s}.npz',bins=bins,values=values,cases=cases[s])
    write_json(OUT/'preprocessing.json',dict(feature_names=names,edges=[e.tolist() for e in edges],
        split_profiles={s:sorted(v) for s,v in sets.items()},windows={s:len(a) for s,a in arrays.items()},
        source_index_sha256=hashlib.sha256((WINDOWS/'dataset-index.json').read_bytes()).hexdigest(),
        seed=SEED,classes=CLASSES,context=10,horizon=10,stride=20))
    print('prepared all 261 features', {s:len(a) for s,a in arrays.items()},flush=True)


def load(split):
    with np.load(OUT/f'{split}.npz') as z:return z['bins'].astype(np.int64),z['cases']


class JointBucket(nn.Module):
    def __init__(self,kind):
        super().__init__()
        self.kind=kind
        if kind=='unum':
            dim=64
            self.backbone=nn.Transformer(d_model=dim,nhead=4,num_encoder_layers=2,
                num_decoder_layers=2,dim_feedforward=256,dropout=.1,batch_first=True)
        else:
            from chronos import ChronosPipeline
            pipe=ChronosPipeline.from_pretrained('amazon/chronos-t5-tiny',device_map='cpu',
                revision='29d808298f1a62493e7b9a5e08529d0d930fa189',local_files_only=True,
                dtype=torch.float32)
            self.backbone=pipe.model.model
            dim=self.backbone.config.d_model
        self.tokens=nn.Embedding(261*52,dim)
        self.position=nn.Parameter(torch.randn(10,dim)*.02)
        self.queries=nn.Parameter(torch.randn(10,dim)*.02)
        self.readout=nn.Linear(dim,261*CLASSES)
        self.register_buffer('offset',torch.arange(261)*52)
        self.dim=dim

    def forward(self,history):
        valid=history>=0
        ids=(history.clamp_min(0)+self.offset).long()
        h=(self.tokens(ids)*valid.unsqueeze(-1)).sum(2)/valid.sum(2).clamp_min(1).sqrt().unsqueeze(-1)
        h=h+self.position
        q=self.queries.unsqueeze(0).expand(len(history),-1,-1)
        if self.kind=='unum':
            hidden=self.backbone(h,q)
        else:
            memory=self.backbone.encoder(inputs_embeds=h,return_dict=True).last_hidden_state
            hidden=self.backbone.decoder(inputs_embeds=q,encoder_hidden_states=memory,return_dict=True).last_hidden_state
        return self.readout(hidden).reshape(len(history),10,261,CLASSES)


def score(model,bins,cases,batch=32,return_flags=False):
    model.eval();parts=[]
    with torch.no_grad():
        for i in range(0,len(bins),batch):
            prediction=model(torch.from_numpy(bins[i:i+batch,:10])).argmax(-1).numpy()
            truth=bins[i:i+batch,10:]
            parts.append(np.where(truth>=0,prediction==truth,False))
    correct=np.concatenate(parts)
    valid=bins[:,10:]>=0
    profiles=sorted(set(cases))
    by_profile=np.array([correct[cases==c].sum()/valid[cases==c].sum() for c in profiles])
    return (float(by_profile.mean()),correct,valid) if return_flags else float(by_profile.mean())


def train(kind,steps=500,lr=3e-4):
    torch.manual_seed(SEED);np.random.seed(SEED);torch.set_num_threads(4)
    train_bins,_=load('train');val_bins,val_cases=load('validation')
    model=JointBucket(kind);optimizer=torch.optim.AdamW(model.parameters(),lr=lr,weight_decay=.01)
    best=-1.;history=[];start=time.perf_counter()
    gen=torch.Generator().manual_seed(SEED)
    for step in range(1,steps+1):
        model.train()
        idx=torch.randint(len(train_bins),(16,),generator=gen).numpy()
        x=torch.from_numpy(train_bins[idx])
        y=x[:,10:].clone();valid=y>=0
        logits=model(x[:,:10])
        loss=F.cross_entropy(logits.flatten(0,2)[valid.flatten()],y.flatten()[valid.flatten()])
        optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1);optimizer.step()
        if step==1 or step%100==0:
            accuracy=score(model,val_bins,val_cases)
            row=dict(step=step,loss=float(loss),validation_accuracy=accuracy)
            history.append(row);write_json(OUT/f'{kind}-history.json',history)
            print(kind,row,flush=True)
            if accuracy>best:
                best=accuracy;torch.save(dict(state=model.state_dict(),step=step,kind=kind),OUT/f'{kind}.pt')
    write_json(OUT/f'{kind}-training.json',dict(best_validation_accuracy=best,steps=steps,
        learning_rate=lr,seconds=time.perf_counter()-start,parameters=sum(p.numel() for p in model.parameters())))


def report():
    torch.set_num_threads(4)
    bins,cases=load('test'); profiles=sorted(set(cases))
    truth=bins[:,10:];valid=truth>=0
    last=np.broadcast_to(bins[:,9:10],truth.shape)
    # Reproduce the earlier baseline: mean continuous history, then bucketize.
    with np.load(OUT/'test.npz') as z: values=z['values']
    history=values[:,:10]
    counts=np.isfinite(history).sum(1,keepdims=True)
    means=np.nansum(history,axis=1,keepdims=True)/counts.clip(min=1)
    mean=np.full((len(bins),1,261),-1,dtype=np.int64)
    edges=json.loads((OUT/'preprocessing.json').read_text())['edges']
    for j,e in enumerate(edges):
        mean[:,0,j]=np.where(counts[:,0,j]>0,np.searchsorted(e,means[:,0,j],side='right'),-1)
    mean=np.broadcast_to(mean,truth.shape)
    predictions={'Persistence':last,'History mean':mean}
    training={}
    for kind in ('unum','chronos'):
        model=JointBucket(kind)
        ckpt=torch.load(OUT/f'{kind}.pt',weights_only=False)
        model.load_state_dict(ckpt['state']);model.eval()
        outputs=[]
        with torch.no_grad():
            for i in range(0,len(bins),16):outputs.append(model(torch.from_numpy(bins[i:i+16,:10])).argmax(-1).numpy())
        predictions['UNUM bucket' if kind=='unum' else 'Chronos fine-tuned']=np.concatenate(outputs)
        training[kind]=json.loads((OUT/f'{kind}-training.json').read_text())
        print('test forecasts complete',kind,flush=True)
    names=list(predictions)
    metrics={};flags={}
    for name,p in predictions.items():
        correct=(p==truth)&valid;flags[name]=correct
        per_profile=np.array([correct[cases==c].sum()/valid[cases==c].sum() for c in profiles])
        per_window=correct.sum((1,2))/valid.sum((1,2)).clip(min=1)
        feature=correct.sum((0,1))/valid.sum((0,1)).clip(min=1)
        horizon=correct.sum((0,2))/valid.sum((0,2)).clip(min=1)
        metrics[name]=dict(profile_macro_accuracy=float(per_profile.mean()),
            point_weighted_accuracy=float(correct.sum()/valid.sum()),
            by_feature=feature.tolist(),by_horizon=horizon.tolist(),
            by_profile=per_profile.tolist(),per_window=per_window.tolist())
    np.savez_compressed(OUT/'test-correct.npz',**flags,truth=truth,valid=valid,cases=cases)
    write_json(OUT/'metrics.json',dict(feature_count=261,test_windows=len(bins),test_profiles=len(profiles),
        evaluated_points=int(valid.sum()),upper_limit=1,methods=metrics))
    fig,ax=plt.subplots(figsize=(9,4),constrained_layout=True)
    vals=[100*metrics[n]['profile_macro_accuracy'] for n in names]
    ax.bar(names,vals);ax.axhline(100,color='black',linestyle='--',label='Full accuracy')
    ax.set(ylabel='Exact bucket accuracy (%)',ylim=(0,105),title='All 261 features; 36 held-out profiles')
    ax.tick_params(axis='x',rotation=12);ax.legend();fig.savefig(OUT/'overall.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(14,8),constrained_layout=True)
    for n in names:
        point=np.array(metrics[n]['per_window'])*100
        axes[0].plot(np.convolve(point,np.ones(101),mode='same')/
            np.convolve(np.ones(len(point)),np.ones(101),mode='same'),label=n)
        axes[1].plot(np.arange(1,11),np.array(metrics[n]['by_horizon'])*100,label=n)
    for ax in axes:ax.axhline(100,color='black',linestyle='--');ax.set(ylabel='Exact bucket accuracy (%)',ylim=(0,103))
    axes[0].set(xlabel='Test window in saved order',title='101-window moving accuracy')
    axes[1].set(xlabel='Future token',title='Accuracy by forecast position');axes[0].legend()
    fig.savefig(OUT/'accuracy-by-point-and-horizon.png',dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(15,4),constrained_layout=True)
    for n in names:ax.plot(np.arange(261),np.array(metrics[n]['by_feature'])*100,label=n,alpha=.85)
    ax.axhline(100,color='black',linestyle='--');ax.set(xlabel='Feature index (see preprocessing.json)',
        ylabel='Exact bucket accuracy (%)',ylim=(0,103),title='Accuracy by feature')
    ax.legend();fig.savefig(OUT/'accuracy-by-feature.png',dpi=160);plt.close(fig)
    rows='\n'.join(f"| {n} | {100*metrics[n]['profile_macro_accuracy']:.2f}% |" for n in names)
    text=f'''# All-feature bucket forecasting

Both models use **all 261 token features** as inputs and predict ten future
bucket indices for every feature from ten history tokens. The UNUM-style model
uses a fresh encoder-decoder Transformer with feature bucket embeddings and a
bucket output head. The Chronos model fine-tunes the cached pretrained
`amazon/chronos-t5-tiny` encoder and decoder with a multivariate bucket adapter.
This adapter changes Chronos from its standard univariate forecasting interface;
its pretrained weights are fine-tuned jointly across all features.

Accuracy is the fraction of observed future data points whose **predicted bucket
index exactly matches** the true index. Missing values are excluded. The main
number averages accuracy across the 36 held-out profiles equally. Full accuracy
is 100%. We use fitting-profile bucket boundaries only. There are
{valid.sum():,} observed feature × token targets in {len(bins):,} test windows.
Both baselines use history only: persistence repeats the last bucket; history
mean rounds the mean of observed history bucket indices.

| Method | Profile-macro bucket accuracy |
|---|---:|
{rows}

![Overall accuracy](overall.png)

![Accuracy by test window and forecast position](accuracy-by-point-and-horizon.png)

![Accuracy by feature](accuracy-by-feature.png)

The exact per-window, per-profile, per-feature and per-horizon values are in
`metrics.json`. `test-correct.npz` stores every individual correctness flag.
The train/validation split is 115/29 profiles from the original training set;
both RNG replicates stay together. Checkpoints were selected by validation
bucket accuracy, never test accuracy. There are no residual outputs or blended
models in this comparison. This is one training seed on CPU.

Run from the repository root:

```bash
python3 scripts/models/train_all_features.py prepare
python3 scripts/models/train_all_features.py train-unum
python3 scripts/models/train_all_features.py train-chronos
python3 scripts/models/train_all_features.py report
```
'''
    (OUT/'README.md').write_text(text)
    (OUT/'SUMMARY.md').write_text(text)
    print({n:round(100*metrics[n]['profile_macro_accuracy'],2) for n in names},flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=('prepare','train-unum','train-chronos','report'))
    p.add_argument('--steps',type=int,default=500)
    a=p.parse_args()
    if a.stage=='prepare':prepare()
    elif a.stage=='train-unum':train('unum',a.steps)
    elif a.stage=='train-chronos':train('chronos',a.steps,1e-4)
    else:report()

if __name__=='__main__':main()
