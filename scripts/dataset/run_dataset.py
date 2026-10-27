#!/usr/bin/env python3
"""Collect the comparison dataset or the broader full dataset, with matched RNG replicates."""
import argparse,concurrent.futures as cf,copy,hashlib,json,random,re,subprocess,sys,time
from pathlib import Path
import campaign_config
import dataset_presets
from experiment_cleanup import clean_experiment, mark_experiment
ROOT=Path(__file__).resolve().parents[2]
BIN=ROOT/'ns-3-dev/build/scratch/sat-bs-handset/ns3.48-sat-bs-handset-optimized'
def profiles():
 base=json.loads((ROOT/'scratch/sat-bs-handset/config/dataset-baseline.json').read_text())
 base=campaign_config.draw(base,random.Random(11),'probe',False,False,'clear',0)
 base['topology']['bsEastOffsetKm']=0;base['application']['dataRate']='10kbps'
 base['metrics']={'physical':True,'payload':False,'intervalS':1}
 # Shared wall-clock windows are necessary for UDP, which has no TCP RTT.
 base['output'].update(tokenMode='fixed',tokenFixedS=0.1,tokenRttLock=False)
 result=[]
 def add(name,group,update):
  cfg=copy.deepcopy(base)
  for section,fields in update.items():cfg.setdefault(section,{}).update(fields)
  result.append((name,group,cfg))
 add('baseline','baseline',{})
 for app in ('file','rpc','stream','iot','voip','iperf','cbr','onoff'):
  appcfg=campaign_config.draw(base,random.Random(11),app if app in ('file','rpc','stream','iot') else 'probe',False,False,'clear',0)['application']
  appcfg.update(workload=app,dataRate='10kbps')
  add('app-'+app,'application',{'application':appcfg})
 for app in ('probe','file','rpc','stream','iot'):
  appcfg=campaign_config.draw(base,random.Random(11),app,False,False,'clear',0)['application'];appcfg.update(transport='udp',dataRate='10kbps')
  add(app+'-udp','transport',{'application':appcfg})
 for app in ('voip','iperf','cbr','onoff'):
  add(app+'-udp','transport',{'application':{'workload':app,'transport':'udp'}})
 for variant in ('TcpCubic','TcpBbr'):
  add('iperf-'+variant,'tcp',{'application':{'workload':'iperf','tcpVariant':variant}})
 for radius in (500,50000):
  for speed in (1.4,15,30,50):
   add(f'motion-{radius}-{speed}','motion',{'topology':{'ueDistanceM':radius,'receiverMobility':True,'ueSpeedMps':speed}})
 add('stationary-50000','motion',{'topology':{'ueDistanceM':50000}})
 add('relay-switch','switching',{'handover':{'enabled':True}})
 for value in (1.5,3):add(f'service-loss-{value}','link',{'radio':{'serviceExcessLossDb':value}})
 for value in (12,24):add(f'feeder-loss-{value}','link',{'radio':{'feederExcessLossDb':value}})
 for relay in (0,1):
  for rate in (5,20,80):
   add(f'bg-1-r{relay}-{rate}k-500','background',{'background':{'count':1,'relay':relay,'dataRate':f'{rate}kbps','distanceM':500}})
  add(f'bg-1-r{relay}-20k-50000','background',{'background':{'count':1,'relay':relay,'dataRate':'20kbps','distanceM':50000}})
  for rate in (5,20):
   add(f'bg-3-r{relay}-{rate}k-500','background',{'background':{'count':3,'relay':relay,'dataRate':f'{rate}kbps','distanceM':500}})
  add(f'bg-3-r{relay}-20k-50000','background',{'background':{'count':3,'relay':relay,'dataRate':'20kbps','distanceM':50000}})
 for transport in ('tcp','udp'):
  add('cbr-dl-'+transport,'direction',{'application':{'workload':'cbr','transport':transport,'direction':'dl'}})
 assert len(result)==50,len(result)
 return result

def plan(out,stem,settings=None):
 settings=settings or dataset_presets.settings()
 out.mkdir(parents=True,exist_ok=True)
 if (out/'manifest.json').exists():raise SystemExit('manifest already exists; use --run to resume')
 mark_experiment(out,'dataset')
 selected,rejected=dataset_presets.expand_profiles(profiles(),settings)
 proofs={name:(dataset_presets.preflight(cfg) if settings['preset']=='full' else campaign_config.preflight(cfg)) for name,_,cfg in selected}
 if not all(proofs.values()):raise RuntimeError('geometry admission failed')
 runs=[]
 for rep in range(settings['replicates']):
  for name,group,cfg in selected:
   cfg=copy.deepcopy(cfg);prefix=f'{stem}-{rep}-{name}';cfg['output']['prefix']=prefix
   if (ROOT/'results'/prefix).exists():raise SystemExit(f'results exist for {prefix}; select --run-prefix')
   path=out/'configs'/(prefix+'.json');path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(cfg,indent=2)+'\n')
   runs.append({'prefix':prefix,'case':name,'group':group,'replicate':rep,'rngRun':settings['rngStart']+rep,
                'config':str(path),'preflight':proofs[name],'knobs':{'workload':cfg['application']['workload'],
                'transport':cfg['application'].get('transport','tcp'),'handover':cfg['handover']['enabled'],
                'tcpVariant':cfg['application']['tcpVariant'],'direction':cfg['application'].get('direction','ul'),
                'dataRate':cfg['application']['dataRate'],'queueSizeBdp':cfg['application']['queueSizeBdp'],
                'queueSizeBytes':cfg['application'].get('queueSizeBytes'),
                'socket-enlargement':cfg['application'].get('socket-enlargement','default'),
                'tcpSocketBufferBytes':cfg['application'].get('tcpSocketBufferBytes'),
                'queueBudgetMultiplier':cfg['application']['queueSizeBdp'],
                'serviceExcessLossDb':cfg['radio']['serviceExcessLossDb'],'feederExcessLossDb':cfg['radio']['feederExcessLossDb'],
                'bandwidthMHz':cfg['radio']['ulBandwidthHz']/1e6,'altitudeKm':cfg['satellite']['altitudeKm'],
                'receiverMobility':cfg['topology']['receiverMobility'],
                'ueSpeedMps':cfg['topology']['ueSpeedMps'] if cfg['topology']['receiverMobility'] else 0,
                'backgroundCount':cfg.get('background',{}).get('count',0)}})
 manifest={'design':f"{len(selected)} profiles x {settings['replicates']} matched RNG replicates; {settings['anchorProfileCount']} controlled anchors, {settings.get('stressProfileCount',0)} stress profiles, plus sampled cross-factor profiles",
           'preset':settings['preset'],'settings':settings,'plannedSize':dataset_presets.planned_size(settings),
           'rejectedProfileCandidates':rejected,
           'candidateRejectionPolicy':'duplicates only in full preset; network service warnings retained',
           'simTimeS':130,'recordWindowS':[20,119],'metricSchema':'minRTT TCP/SINR/radio-event tokens with last-completed one-second KPIs and path samples',
           'progressFeatures':{'tcp_pending_bytes':'TCP bytes queued or awaiting ACK',
              'tcp_ack_age_s':'seconds since forward TCP transmit-buffer head progress; missing before first observation',
              'tcp_no_progress_s':'seconds continuously backlogged without forward ACK progress; zero when idle',
              'tcp_rtt_update_age_s':'seconds since a changed positive LastRTT trace value; not a packet RTT sample count'},
           'runs':runs}
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print('planned',len(runs),'runs',flush=True)

def run(out,jobs,records=None,verify_sources=True):
 manifest=json.loads((out/'manifest.json').read_text());(out/'logs').mkdir(exist_ok=True)
 if verify_sources:
  sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scratch/sat-bs-handset').rglob('*') if p.is_file() and p.suffix in ('.cc','.h')}
  sources['binary']=hashlib.sha256(BIN.read_bytes()).hexdigest()
  sources['nrLibrary']=hashlib.sha256((ROOT/'ns-3-dev/build/lib/libns3.48-nr-optimized.so').read_bytes()).hexdigest()
  sources['ns3Patch']=hashlib.sha256((ROOT/'patches/ns3-v3.48-local-ntn.patch').read_bytes()).hexdigest()
  sources['nrPatch']=hashlib.sha256((ROOT/'patches/nr-v5.1-local-ntn.patch').read_bytes()).hexdigest()
  sources['configs']={r['prefix']:hashlib.sha256(Path(r['config']).read_bytes()).hexdigest() for r in manifest['runs']}
  stamp=out/'source-hashes.json'
  if stamp.exists() and json.loads(stamp.read_text())!=sources:raise SystemExit('source changed since run started; use --clean to restart this campaign or select a fresh directory')
  stamp.write_text(json.dumps(sources,indent=2)+'\n')
  for name in sources:
   if name in ('configs','binary','nrPatch','nrLibrary','ns3Patch'):continue
   snapshot=out/'source-snapshot'/name;snapshot.parent.mkdir(parents=True,exist_ok=True)
   snapshot.write_bytes((ROOT/name).read_bytes())
 def one(rec):
  prefix=rec['prefix'];status=out/'logs'/(prefix+'.json')
  raw_dir=ROOT/'results'/prefix
  required=[raw_dir/(prefix+'-'+suffix) for suffix in ('tcp.csv','summary.txt','app.txt')]
  if status.exists() and all(path.exists() for path in required):
   return json.loads(status.read_text())
  before=time.monotonic()
  with (out/'logs'/(prefix+'.log')).open('w') as log:
   cfg=json.loads(Path(rec['config']).read_text())
   command=[str(BIN),'--config='+rec['config'],'--RngRun='+str(rec['rngRun'])]+dataset_presets.socket_buffer_arguments(cfg)
   completed=subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
  row={**rec,'exit':completed.returncode,'wallS':round(time.monotonic()-before,2),'command':command}
  status.write_text(json.dumps(row,indent=2)+'\n')
  print(prefix,'exit',completed.returncode,'seconds',row['wallS'],flush=True)
  return row
 with cf.ThreadPoolExecutor(max_workers=jobs) as pool:executed=list(pool.map(one,records if records is not None else manifest['runs']))
 if records is None:(out/'run-outcomes.json').write_text(json.dumps({'runs':executed},indent=2)+'\n')
 if any(r['exit'] not in (0,1) for r in executed):raise SystemExit('simulation crashes present; inspect run-outcomes.json')
 print('completed',len(executed),'runs; application criteria passed',sum(r['exit']==0 for r in executed),flush=True)
 return executed

def normalize_reports(rec):
 """Mask legacy text bookkeeping for UDP and label native applications from their config."""
 cfg=json.loads(Path(rec['config']).read_text());p=rec['prefix'];d=ROOT/'results'/p
 workload=cfg['application']['workload'];udp=cfg['application'].get('transport','tcp')=='udp'
 native=workload in ('voip','iperf','cbr','onoff')
 hashes={}
 for kind in ('summary','app'):
  target=d/(p+'-'+kind+'.txt');raw=d/(p+'-'+kind+'-raw.txt')
  if not raw.exists():raw.write_bytes(target.read_bytes())
  lines=raw.read_text().splitlines();normalized=[]
  for line in lines:
   if native and ('workload=' in line):line=re.sub(r'workload=\S+','workload='+workload,line)
   if native and line.startswith('application criterion='):line='application criterion=accepted_source_bytes>0 and delivered_sink_bytes>0'
   if udp and line.startswith('metrics.sage.'):
    line=re.sub(r'=([-+0-9.eE]+)(?:\([A-Za-z]+\))?', '=nan',line)
   normalized.append(line)
  target.write_text('\n'.join(normalized)+'\n')
  hashes[kind]={'raw':hashlib.sha256(raw.read_bytes()).hexdigest(),'normalized':hashlib.sha256(target.read_bytes()).hexdigest()}
 (d/'report-normalization.json').write_text(json.dumps({'transport':cfg['application'].get('transport','tcp'),
   'workload':workload,'native':native,'reason':'config labels and UDP applicability; measured CSV values unchanged',
   'sha256':hashes},indent=2)+'\n')

def prepare_features(rec):
 normalize_reports(rec)
 p=rec['prefix'];d=ROOT/'results'/p;marker=d/'legacy-post-complete.json'
 script_hash=hashlib.sha256(b''.join((ROOT/'scripts/dataset'/f).read_bytes() for f in
      ['bucketize_metrics.py','fold_tokens.py','metric_io.py','radio_tokens.py'])).hexdigest()
 signature={'config':hashlib.sha256(Path(rec['config']).read_bytes()).hexdigest(),'scripts':script_hash}
 feature=d/(p+'-features.csv')
 if marker.exists() and feature.exists():
  previous=json.loads(marker.read_text())
  if previous.get('signature')==signature and previous.get('featureHash')==hashlib.sha256(feature.read_bytes()).hexdigest():return
 args=['python3',str(ROOT/'scripts/dataset/bucketize_metrics.py'),'--config',rec['config'],'--tcp',str(d/(p+'-tcp.csv')),
       '--sinr-dl',str(d/(p+'-sinr-dl.csv')),'--sinr-ul',str(d/(p+'-sinr-ul.csv')),'--num-buckets','50']
 out=Path(rec['config']).parent.parent
 with (out/'logs'/(p+'-post.log')).open('w') as log:subprocess.run(args,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 marker.write_text(json.dumps({'signature':signature,'featureHash':hashlib.sha256(feature.read_bytes()).hexdigest()},indent=2)+'\n')

def post(out,jobs):
 manifest=json.loads((out/'manifest.json').read_text())
 outcomes=json.loads((out/'run-outcomes.json').read_text())['runs']
 if any(r['exit'] not in (0,1) for r in outcomes):raise ValueError('simulator crashes must be investigated before dataset export')
 labels={r['prefix']:{'exit':r['exit'],'applicationPassed':r['exit']==0,
    'case':r['case'],'group':r['group'],'replicate':r['replicate']} for r in outcomes}
 if set(labels)!={r['prefix'] for r in manifest['runs']}:raise ValueError('incomplete run outcome coverage')
 (out/'dataset-outcomes.json').write_text(json.dumps({'policy':'retain all completed network outcomes; missing observations remain missing','runs':labels},indent=2)+'\n')
 with cf.ThreadPoolExecutor(max_workers=jobs) as pool:list(pool.map(prepare_features,manifest['runs']))
 if manifest.get('settings',{}).get('shardRuns',0):
  from dataset_storage import post_sharded
  post_sharded(ROOT,out,manifest)
  return
 # Preserve matched replicate groups across the split (replicate 3 in older manifests).
 # This avoids the same RNG stream appearing on both sides via controlled contrasts.
 import numpy as np
 from make_datasets import load_run_windows,fit_boundaries_on_train,tokenize_multi,save_split
 files=[ROOT/'results'/r['prefix']/(r['prefix']+'-features.csv') for r in manifest['runs']]
 values,names,ids,_=load_run_windows(files,seq_len=20,stride=10)
 if set(ids)!=set(labels):raise ValueError('a completed run has no training windows')
 train_rng,test_rng=dataset_presets.split_rng_runs(manifest)
 held_out=dataset_presets.test_replicates(manifest)
 test_prefixes={r['prefix'] for r in manifest['runs'] if dataset_presets.is_test_run(r,test_rng)}
 test=np.array([r in test_prefixes for r in ids]);edges=fit_boundaries_on_train(values[~test],names,50)
 tokens,columns=tokenize_multi(values,names,edges)
 for split,mask in [('train',~test),('test',test)]:
  selected=[r for r,m in zip(ids,mask) if m]
  save_split(out/('dataset-'+split),tokens[mask],10,columns,names,selected,[False]*len(selected),
             {'split':split,'splitUnit':'matched RNG run','testReplicates':sorted(held_out),
              'testRngRuns':sorted(test_rng),'trainRngRuns':sorted(train_rng),'context':10,'future':10})
 (out/'dataset-boundaries.json').write_text(json.dumps(edges,indent=2)+'\n')
 (out/'post-complete.json').write_text(json.dumps({'runs':len(files),'shape':list(values.shape)},indent=2)+'\n')
 print('postprocessed',len(files),'runs',values.shape,flush=True)

def main():
 """Run the complete dataset workflow by default, or only explicitly selected stages."""
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--out-dir',type=Path)
 p.add_argument('--clean',action='store_true',help='delete this campaign and its recorded raw outputs before replanning; omit to resume')
 p.add_argument('--preset',choices=('comparison','full'),help='comparison: 200 runs (default); full: settings in full_dataset.json')
 p.add_argument('--design-file',type=Path,help='alternate full-preset JSON settings for a new campaign')
 p.add_argument('--profile-count',type=int,help='full-preset profile count, including 49 switching-enabled anchors and any enabled stress profiles')
 p.add_argument('--replicates',type=int,help='matched RNG repeats per profile; comparison defaults to 4, full to 20')
 p.add_argument('--design-seed',type=int,help='seed for sampling the full-preset configurations')
 p.add_argument('--target-windows',type=int,help='increase the full-preset profile count to reach this many 20-token windows')
 p.add_argument('--describe',action='store_true',help='print settings and planned size without creating files or running simulations')
 p.add_argument('--run-prefix',default='full',help='unique result prefix for a fresh collection')
 p.add_argument('--plan',action='store_true',help='only plan configurations, unless other stages are selected')
 p.add_argument('--run',action='store_true',help='execute an existing plan')
 p.add_argument('--post',action='store_true',help='prepare features and train/test datasets')
 p.add_argument('--analyze',action='store_true',help='audit the dataset and generate comparison plots')
 p.add_argument('--report',action='store_true',help='write the results summary')
 p.add_argument('--jobs',type=int,default=8)
 p.add_argument('--stream',action='store_true',help='full preset: run, tokenize and remove raw files in bounded parts; split saved parts at the end')
 p.add_argument('--batch-runs',type=int,default=4,help='runs per streaming part after calibration (default: 4)')
 p.add_argument('--calibration-runs',type=int,default=12,help='spread-out training runs used to fit shared token boundaries (default: 12)')
 p.epilog='Without stage flags, creates a plan if missing and runs all stages. --stream instead processes bounded parts and writes indexed train/test shards. Existing plans resume; --clean restarts in the same directory.'
 a=p.parse_args()
 if a.jobs<1:p.error('--jobs must be positive')
 if a.batch_runs<1 or a.calibration_runs<1:p.error('--batch-runs and --calibration-runs must be positive')
 if a.stream and (a.post or a.analyze or a.report):p.error('--stream includes tokenization; --post, --analyze and --report require retained raw files')
 if a.describe:
  if a.clean or any((a.plan,a.run,a.post,a.analyze,a.report)):p.error('--describe cannot be combined with --clean or execution stages')
  try:configuration=dataset_presets.settings(a.preset or 'comparison',a.profile_count,a.replicates,a.design_seed,a.target_windows,a.design_file)
  except ValueError as e:p.error(str(e))
  print(json.dumps({'settings':configuration,'plannedSize':dataset_presets.planned_size(configuration)},indent=2));return
 if a.out_dir is None:p.error('--out-dir is required unless --describe is used')
 out=a.out_dir.resolve()
 try:previous=json.loads((out/'manifest.json').read_text()) if (out/'manifest.json').exists() else {}
 except json.JSONDecodeError:
  if not a.clean:p.error('saved manifest is incomplete; use --clean to restart')
  previous={}
 if not any((a.plan,a.run,a.post,a.analyze,a.report)):
  a.plan=a.clean or not (out/'manifest.json').exists()
  a.run=True
  if not a.stream:a.post=a.analyze=a.report=True
 if a.clean:
  if not (a.plan or a.run):p.error('--clean requires planning or simulation, not postprocessing alone')
  a.plan=True
 if a.plan:
  if not a.run_prefix or any(not (c.isalnum() or c in '-_') for c in a.run_prefix):p.error('invalid --run-prefix')
  try:configuration=dataset_presets.settings(a.preset or previous.get('preset','comparison'),a.profile_count,a.replicates,a.design_seed,a.target_windows,a.design_file)
  except ValueError as e:p.error(str(e))
  if a.clean:
   try:clean_experiment(out,ROOT/'results','dataset')
   except ValueError as e:p.error(str(e))
  plan(out,a.run_prefix,configuration)
 else:
  if a.design_file:
   try:digest=hashlib.sha256(a.design_file.read_bytes()).hexdigest()
   except OSError as e:p.error(str(e))
   if digest!=previous.get('settings',{}).get('designFileSha256'):
    p.error('--design-file differs from the saved campaign settings')
  overrides={'profileCount':a.profile_count,'replicates':a.replicates,
             'designSeed':a.design_seed,'targetWindows':a.target_windows}
  if any(value is not None for value in overrides.values()):
   if not a.stream:p.error('design overrides require the planning stage')
   saved=previous.get('settings',{})
   if any(saved.get(key)!=value for key,value in overrides.items() if value is not None):
    p.error('stream design overrides disagree with the saved manifest')
  if a.preset and a.preset!=json.loads((out/'manifest.json').read_text()).get('preset','comparison'):
   p.error('--preset disagrees with the saved manifest; resume using its existing design')
 if a.stream and a.run:
  manifest=json.loads((out/'manifest.json').read_text())
  if manifest.get('preset')!='full':p.error('--stream requires the full preset')
  from dataset_storage import stream_campaign
  stream_campaign(ROOT,out,manifest,a.batch_runs,a.calibration_runs,
                  lambda records,verify:run(out,a.jobs,records,verify),prepare_features)
 elif a.run:run(out,a.jobs)
 if a.post:post(out,a.jobs)
 if a.analyze:
  subprocess.run([sys.executable,str(Path(__file__).with_name('analyze_dataset.py')),
                  '--campaign',str(out),'--jobs',str(min(a.jobs,4))],cwd=ROOT,check=True)
 if a.report:
  subprocess.run([sys.executable,str(Path(__file__).with_name('build_report.py')),
                  '--campaign',str(out)],cwd=ROOT,check=True)
if __name__=='__main__':main()
