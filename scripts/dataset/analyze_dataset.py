#!/usr/bin/env python3
"""Audit the full dataset and render a small set of comparisons per experiment knob."""
import argparse,csv,hashlib,json,math,re,time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
ROOT=Path(__file__).resolve().parents[2]
RADIO_FEATURES=['pdcp_volume_dl_kB','pdcp_volume_ul_kB','rlc_sdu_delay_dl_us','prbs_dl','prbs_ul',
                'pdcp_throughput_dl_Mbps','pdcp_throughput_ul_Mbps','sinr_dl_dB','sinr_ul_dB',
                'mcs_dl','mcs_ul','tb_decode_fail_dl_fraction','tb_decode_fail_ul_fraction',
                'harq_nack_dl_count','harq_nack_ul_count','bsr_max_reported_level',
                'propagation_delay_s','timing_advance_s','ul_slot_phase_error_ns',
                'ue_speed_mps','relay_index','ues_sharing_feeder','ues_sharing_scheduler']

def records(path):
 with path.open() as f:return list(csv.DictReader(line for line in f if line.strip() and not line.startswith('#')))
def finite_mean(values):
 a=np.asarray(values,dtype=float);a=a[np.isfinite(a)];return float(np.mean(a)) if len(a) else math.nan
def percentile(values,p):
 a=np.asarray(values,dtype=float);a=a[np.isfinite(a)];return float(np.percentile(a,p)) if len(a) else math.nan
def clean(value):
 if isinstance(value,float) and not math.isfinite(value):return None
 if isinstance(value,dict):return {k:clean(v) for k,v in value.items()}
 if isinstance(value,list):return [clean(v) for v in value]
 return value

def slot_phase_error(value):
 """Resolve the SFN/start-time transition ambiguity modulo the 1 ms mu=0 slot."""
 return (float(value)+500000)%1000000-500000

def checked_summary(rec):
 if 'exit' not in rec:
  path=Path(rec['config']).parent.parent/'logs'/(rec['prefix']+'.json')
  while not path.exists():time.sleep(1)
  rec=json.loads(path.read_text())
 assert rec['exit'] in (0,1),(rec['prefix'],'simulation crashed')
 return rec,summarize(rec)

def summarize(rec):
 p=rec['prefix'];d=ROOT/'results'/p;cfg=json.loads(Path(rec['config']).read_text())
 k=records(d/(p+'-kpis.csv'));primary=[r for r in k if r['ue_index']=='0' and 20<float(r['time_s'])<=119]
 assert len(primary)==99,(p,'incomplete KPI interval coverage',len(primary))
 rnti=primary[0]['rnti'];tb=records(d/(p+'-transport-blocks.csv'));ev=records(d/(p+'-radio-events.csv'))
 paths=records(d/'ue-paths.csv');assert len(paths)>=130*(1+cfg.get('background',{}).get('count',0)),p
 assert all(r['visible']=='1' for r in paths),(p,'unexpected outage')
 # Reconcile cumulative SDU bytes and actual scheduled PRB counts for every UE.
 for identity in {r['rnti'] for r in k if int(r['rnti'])>0}:
  for direction,suffix in [('DL','Dl'),('UL','Ul')]:
   measured=sum(float(r['RRU.PrbTot'+suffix]) for r in k if r['rnti']==identity)
   expected=sum(float(r['prbs']) for r in tb if r['event']=='TX' and r['direction']==direction and r['rnti']==identity)
   assert math.isclose(measured,expected),(p,'PRB accounting',identity,direction)
   measured=sum(float(r['DRB.PdcpSduVolume'+direction+'_kB'])*1000 for r in k if r['rnti']==identity)
   expected=sum(int(r['size_bytes'])-2 for r in ev if r['event']=='PDCP_TX' and r['rnti']==identity and r['direction']==direction and int(r['lcid'])>2)
   assert math.isclose(measured,expected,abs_tol=1e-5),(p,'SDU accounting',identity,direction)
 result={key:rec[key] for key in ('prefix','case','group','replicate','rngRun')}
 result['exit']=rec['exit'];result.update(workload=cfg['application']['workload'],transport=cfg['application'].get('transport','tcp'),tcp=cfg['application']['tcpVariant'],direction=cfg['application'].get('direction','ul'))
 result.update(speed_mps=cfg['topology']['ueSpeedMps'] if cfg['topology']['receiverMobility'] else 0,radius_m=cfg['topology']['ueDistanceM'],background_count=cfg.get('background',{}).get('count',0),background_relay=cfg.get('background',{}).get('relay',0),background_distance_m=cfg.get('background',{}).get('distanceM',0))
 result['background_offered_kbps']=float(cfg.get('background',{}).get('dataRate','0kbps').removesuffix('kbps'))*result['background_count']
 for direction in ('Dl','Ul'):
  result[direction.lower()+'_goodput_kbps']=finite_mean([float(r['DRB.UEThp'+direction+'_Mbps'])*1000 for r in primary])
 delay_n=sum(int(r['dl_delay_samples']) for r in primary)
 result['dl_sdu_delay_ms']=sum(float(r['DRB.RlcSduDelayDl_us'])*int(r['dl_delay_samples']) for r in primary if int(r['dl_delay_samples']))/delay_n/1000 if delay_n else math.nan
 result['background_goodput_kbps']=sum(float(r['DRB.UEThpUl_Mbps'])*1000 for r in k if r['ue_index']!='0' and 20<float(r['time_s'])<=119)/99
 for direction in ('DL','UL'):
  received=[r for r in tb if r['event']=='RX' and r['direction']==direction and r['rnti']==rnti and 20<=float(r['time_s'])<119]
  sent=[r for r in tb if r['event']=='TX' and r['direction']==direction and r['rnti']==rnti and 20<=float(r['time_s'])<119]
  result[direction.lower()+'_sinr_db']=finite_mean([float(r['sinr_db']) for r in received])
  result[direction.lower()+'_tb_fail_pct']=finite_mean([100*(1-float(r['crc_pass_model'])) for r in received])
  result[direction.lower()+'_mcs']=finite_mean([float(r['mcs']) for r in sent])
 boundary=[float(r['value']) for r in ev if r['event']=='NTN_BOUNDARY_NS']
 result['max_boundary_overlap_ns']=max(boundary,default=0)
 assert result['max_boundary_overlap_ns']<=100,(p,'boundary exceeded')
 rows=records(d/(p+'-tcp.csv'))
 if result['transport']=='udp':
  assert all(math.isnan(float(v)) for row in rows for key,v in row.items() if key not in ('time_s','sinr_dl_db','sinr_ul_db')),(p,'UDP has fabricated TCP observations')
 srtt=np.array([float(r['srtt'])*100 for r in rows]);times=np.array([float(r['time_s']) for r in rows]);srtt[srtt<=0]=np.nan
 result['srtt_ms']=finite_mean(srtt);result['srtt_p90_ms']=percentile(srtt,90)
 primary_paths=[r for r in paths if r['ue_index']=='0' and 20<=float(r['time_s'])<=119]
 result['propagation_rtt_ms']=finite_mean([2000*float(r['propagation_delay_s']) for r in primary_paths])
 result['primary_movement_m']=float(np.linalg.norm(np.array([float(primary_paths[-1][x]) for x in ('x_m','y_m','z_m')])-np.array([float(primary_paths[0][x]) for x in ('x_m','y_m','z_m')])))
 result['max_ul_slot_arrival_error_ns']=max(abs(slot_phase_error(r['ul_slot_arrival_error_ns'])) for r in paths if float(r['time_s'])>1)
 assert result['max_ul_slot_arrival_error_ns']<200,(p,'NTN slot clock drift')
 result['min_elevation_deg']=min(float(r['service_elevation_deg']) for r in paths)
 bg_delivered=[]
 for i in range(result['background_count']):
  a=records(d/f'background-{i}-application.csv');bg_delivered.append(int(a[-1]['rx_bytes']))
 assert all(n>0 for n in bg_delivered),(p,'background UE delivered nothing')
 result['background_bytes_delivered']=sum(bg_delivered)
 # An independent 1 s tensor exposes new numeric radio/KPI data without changing legacy heads.
 radio=np.full((99,len(RADIO_FEATURES)),np.nan)
 kpi_columns=['DRB.PdcpSduVolumeDL_kB','DRB.PdcpSduVolumeUL_kB','DRB.RlcSduDelayDl_us',
              'RRU.PrbTotDl','RRU.PrbTotUl','DRB.UEThpDl_Mbps','DRB.UEThpUl_Mbps']
 for i,r in enumerate(primary):radio[i,:7]=[float(r[x]) for x in kpi_columns]
 tb_bins=[[] for _ in range(99)];event_bins=[[] for _ in range(99)]
 for r in tb:
  b=int(float(r['time_s']))-20
  if r['rnti']==rnti and 0<=b<99:tb_bins[b].append(r)
 for r in ev:
  b=int(float(r['time_s']))-20
  if r['rnti']==rnti and 0<=b<99:event_bins[b].append(r)
 for b in range(99):
  start=20+b
  for j,direction in enumerate(('DL','UL')):
   rx=[r for r in tb_bins[b] if r['event']=='RX' and r['direction']==direction and r['rnti']==rnti and start<=float(r['time_s'])<start+1]
   tx=[r for r in tb_bins[b] if r['event']=='TX' and r['direction']==direction and r['rnti']==rnti and start<=float(r['time_s'])<start+1]
   radio[b,7+j]=finite_mean([float(r['sinr_db']) for r in rx])
   radio[b,9+j]=finite_mean([float(r['mcs']) for r in tx])
   radio[b,11+j]=finite_mean([1-float(r['crc_pass_model']) for r in rx])
   radio[b,13+j]=sum(r['event']=='HARQ_GENERATED' and r['direction']==direction and r['rnti']==rnti and r['value']=='0' and start<=float(r['time_s'])<start+1 for r in event_bins[b])
  levels=[]
  for r in event_bins[b]:
   if r['rnti']==rnti and 'bsr_levels=' in r['details'] and start<=float(r['time_s'])<start+1:
    levels.extend(int(x) for x in r['details'].split('bsr_levels=')[-1].split(':') if x)
  if levels:radio[b,15]=max(levels)
  path=next(r for r in primary_paths if float(r['time_s'])==start)
  radio[b,16:]=[slot_phase_error(path[x]) if x=='ul_slot_arrival_error_ns' else float(path[x]) for x in ('propagation_delay_s','timing_advance_s','ul_slot_arrival_error_ns',
               'speed_mps','relay_index','ues_sharing_feeder','ues_sharing_scheduler')]
 with (d/'radio-kpi-features.csv').open('w') as f:
  w=csv.writer(f);w.writerow(['start_s','end_s']+RADIO_FEATURES)
  for i,values_ in enumerate(radio):w.writerow([20+i,21+i]+list(values_))
 log=(Path(rec['config']).parent.parent/'logs'/(p+'.log')).read_text()
 switches=re.findall(r'handover complete timeS=([0-9.]+)',log);result['switches']=len(switches)
 assert len(switches)==int(cfg['handover']['enabled']),(p,'switch count')
 series=(times,srtt,float(switches[0]) if switches else None) if rec['case'] in ('baseline','relay-switch') else None
 return result,series,radio

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--campaign',type=Path,required=True);ap.add_argument('--watch',action='store_true');ap.add_argument('--jobs',type=int,default=4);a=ap.parse_args();campaign=a.campaign.resolve()
 outcomes=json.loads((campaign/('manifest.json' if a.watch else 'run-outcomes.json')).read_text())['runs'];assert len(outcomes)==200
 rows=[];series={};radio_runs=[]
 with ProcessPoolExecutor(max_workers=a.jobs) as pool:
  for i,(rec,summary) in enumerate(pool.map(checked_summary,outcomes)):
   outcomes[i]=rec;row,s,radio=summary;rows.append(row);radio_runs.append(radio)
   if s:series[(rec['case'],rec['replicate'])]=s
   if i%20==0:print('audited',i+1,'/',len(outcomes),flush=True)
 radio_values=np.stack(radio_runs)
 radio_test=np.array([r['replicate']==3 for r in outcomes])
 for split,mask in [('train',~radio_test),('test',radio_test)]:
  np.savez_compressed(campaign/('radio-'+split+'.npz'),values=radio_values[mask],
      feature_names=np.array(RADIO_FEATURES),run_ids=np.array([r['prefix'] for r in outcomes])[mask],
      start_s=np.arange(20,119),end_s=np.arange(21,120))
 (campaign/'run-summary.json').write_text(json.dumps(clean(rows),indent=2)+'\n')
 with (campaign/'run-summary.csv').open('w') as f:
  writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 # Audit every legacy feature's missingness and spread over all 200 runs.
 audit=[]
 for rec in outcomes:
  p=rec['prefix']
  if a.watch:
   while not (ROOT/'results'/p/'legacy-post-complete.json').exists():time.sleep(1)
  features=records(ROOT/'results'/p/(p+'-features.csv'))
  for key in features[0]:
   if key in ('token','t_start_s','t_end_s','window_s','n_samples'):continue
   values=np.array([float(r[key]) if r[key] else np.nan for r in features]);observed=values[np.isfinite(values)]
   audit.append({'prefix':p,'metric':key,'missing_fraction':float(np.mean(~np.isfinite(values))),
                 'mean':finite_mean(values),'p10':percentile(values,10),'p90':percentile(values,90),'observed':len(observed)})
 for rec,radio in zip(outcomes,radio_runs):
  for j,key in enumerate(RADIO_FEATURES):
   values=radio[:,j];observed=values[np.isfinite(values)]
   audit.append({'prefix':rec['prefix'],'metric':'radio.'+key,
      'missing_fraction':float(np.mean(~np.isfinite(values))),'mean':finite_mean(values),
      'p10':percentile(values,10),'p90':percentile(values,90),'observed':len(observed)})
 with (campaign/'all-metrics-audit.csv').open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(audit[0]));w.writeheader();w.writerows(audit)
 # Per-run summaries are the statistical unit; ranges show variation across four RNG replicates.
 cases={name:[r for r in rows if r['case']==name] for name in {r['case'] for r in rows}}
 def med(name,key):return float(np.nanmedian([r[key] for r in cases[name]]))
 def values(name,key):return [r[key] for r in cases[name]]
 plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.dpi':140})
 plotdir=campaign/'figures';plotdir.mkdir(exist_ok=True);pdf=PdfPages(plotdir/'important-comparisons.pdf')
 def save(fig,name):
  fig.tight_layout();fig.savefig(plotdir/(name+'.png'),dpi=170,bbox_inches='tight');fig.savefig(plotdir/(name+'.pdf'),bbox_inches='tight');pdf.savefig(fig,bbox_inches='tight');plt.close(fig)
 def bars(ax,names,key,labels=None):
  x=np.arange(len(names));y=[med(n,key) for n in names];ax.bar(x,y,color='#297f9f')
  for i,n in enumerate(names):ax.scatter([i]*4,values(n,key),s=18,color='#173e50',zorder=3)
  ax.set_xticks(x,labels or names,rotation=25,ha='right');ax.grid(axis='y',alpha=.2)
 apps=['baseline']+['app-'+x for x in ('file','rpc','stream','iot','voip','iperf','cbr','onoff')]
 labels=['probe','file','RPC','stream','IoT','VoIP','bulk','CBR','OnOff']
 fig,ax=plt.subplots(1,2,figsize=(12,4));bars(ax[0],apps,'ul_goodput_kbps',labels);bars(ax[1],apps,'ul_mcs',labels)
 ax[0].set_yscale('log');ax[0].set_ylabel('Delivered UL PDCP SDU rate (kbit/s, log)');ax[1].set_ylabel('Mean scheduled UL MCS');fig.suptitle('Applications: delivered rate and scheduled MCS (TCP)');save(fig,'applications')
 fig,ax=plt.subplots(1,2,figsize=(10,4));speeds=[0,1.4,15,30,50]
 for radius,color in [(500,'#297f9f'),(50000,'#d47c36')]:
  names=[('baseline' if radius==500 else 'stationary-50000')]+[f'motion-{radius}-{speed}' for speed in (1.4,15,30,50)]
  for panel,key in zip(ax,('srtt_ms','ul_sinr_db')):
   reference={r['replicate']:r[key] for r in cases[names[0]]}
   differences=[[r[key]-reference[r['replicate']] for r in cases[n]] for n in names]
   panel.plot(speeds,[float(np.median(v)) for v in differences],'-o',label=f'{radius/1000:g} km disk',color=color)
   panel.fill_between(speeds,[min(v) for v in differences],[max(v) for v in differences],color=color,alpha=.12)
   panel.axhline(0,color='gray',ls='--',lw=.8)
   panel.set_xlabel('UE speed (m/s)');panel.grid(alpha=.2)
 ax[0].set_ylabel('TCP SRTT change from stationary (ms)');ax[1].set_ylabel('UL SINR change from stationary (dB)');ax[0].legend();fig.suptitle('Motion: same-radius stationary controls; bands span four runs');save(fig,'motion')
 fig,ax=plt.subplots(1,2,figsize=(11,4))
 for name,records_ in cases.items():
  r=records_[0]
  if r['group']!='background':continue
  for panel,key in zip(ax,('srtt_ms','ul_goodput_kbps')):
   panel.scatter(r['background_offered_kbps'],med(name,key),marker='o' if r['background_count']==1 else '^',
     color='#297f9f' if r['background_relay']==0 else '#d47c36',s=70,
     facecolors='none' if r['background_distance_m']==50000 else ('#297f9f' if r['background_relay']==0 else '#d47c36'))
 for panel,key in zip(ax,('srtt_ms','ul_goodput_kbps')):
  panel.axhline(med('baseline',key),color='gray',ls='--',label='No background');panel.set_xlabel('Total offered background application rate (kbit/s)');panel.grid(alpha=.2)
 ax[0].set_ylabel('Primary mean TCP SRTT (ms)');ax[1].set_ylabel('Primary UL PDCP SDU rate (kbit/s)');fig.suptitle('Background: blue relay 0, orange relay 1; circle 1 UE, triangle 3 UEs\nOpen markers: 50 km separation; filled: 500 m');save(fig,'background')
 fig,ax=plt.subplots(1,2,figsize=(10,4));algs=['app-iperf','iperf-TcpCubic','iperf-TcpBbr'];labs=['NewReno','CUBIC','BBR']
 bars(ax[0],algs,'ul_goodput_kbps',labs);bars(ax[1],algs,'srtt_p90_ms',labs);ax[0].set_ylabel('UL PDCP SDU rate (kbit/s)');ax[1].set_ylabel('TCP SRTT p90 (ms)');fig.suptitle('TCP algorithms: identical unpaced bulk application');save(fig,'tcp-algorithms')
 fig,ax=plt.subplots(1,2,figsize=(10,4));names=['baseline','probe-udp','app-cbr','cbr-udp'];labels=['Probe TCP','Probe UDP','CBR TCP','CBR UDP']
 bars(ax[0],names,'ul_goodput_kbps',labels);bars(ax[1],names,'ul_tb_fail_pct',labels);ax[0].set_ylabel('UL PDCP SDU rate (kbit/s)');ax[1].set_ylabel('Received UL TB decode failure (%)');fig.suptitle('Transport: matched 10 kbit/s paced sources; TCP RTT is NaN for UDP');save(fig,'udp-vs-tcp')
 fig,ax=plt.subplots(1,2,figsize=(10,4));names=['baseline','service-loss-1.5','service-loss-3','feeder-loss-12','feeder-loss-24'];labels=['clear','service +1.5 dB','service +3 dB','feeder +12 dB','feeder +24 dB']
 bars(ax[0],names,'ul_sinr_db',labels);bars(ax[1],names,'srtt_ms',labels);ax[0].set_ylabel('Mean received UL SINR (dB)');ax[1].set_ylabel('Mean TCP SRTT (ms)');fig.suptitle('Link conditions: fixed service/feeder attenuation');save(fig,'link-conditions')
 fig,ax=plt.subplots(1,2,figsize=(10,4));centres=np.arange(-10,16)+.5
 for name,color,label in [('relay-switch','#d47c36','Switch'),('baseline','#297f9f','No switch')]:
  aligned=[]
  for rep in range(4):
   t,y,event=series[(name,rep)];event=series[('relay-switch',rep)][2]
   before=finite_mean(y[(t>=event-10)&(t<event-2)])
   aligned.append([finite_mean(y[(t>=event+b)&(t<event+b+1)])-before for b in range(-10,16)])
  aligned=np.array(aligned);ax[0].plot(centres,np.nanmedian(aligned,axis=0),label=label,color=color);ax[0].fill_between(centres,np.nanmin(aligned,axis=0),np.nanmax(aligned,axis=0),alpha=.15,color=color)
 ax[0].axvline(0,color='gray',ls='--');ax[0].set_xlabel('Seconds from switch / matched control time');ax[0].set_ylabel('TCP SRTT change from pre-event mean (ms)');ax[0].legend()
 bars(ax[1],['baseline','relay-switch'],'propagation_rtt_ms',['No switch','Switch']);ax[1].set_ylabel('Mean geometric propagation RTT (ms)');fig.suptitle('Relay selection: protocol latency and geometric light time');save(fig,'switching');pdf.close()
 # Verify source hashes and train/test separation, including missing TCP data for UDP.
 source=json.loads((campaign/'source-hashes.json').read_text())
 for name,digest in source.items():
  if name in ('configs','binary','nrPatch','nrLibrary','ns3Patch'):continue
  assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
 assert hashlib.sha256((ROOT/'ns-3-dev/build/lib/libns3.48-nr-optimized.so').read_bytes()).hexdigest()==source['nrLibrary']
 assert hashlib.sha256((ROOT/'patches/ns3-v3.48-local-ntn.patch').read_bytes()).hexdigest()==source['ns3Patch']
 assert hashlib.sha256((ROOT/'patches/nr-v5.1-local-ntn.patch').read_bytes()).hexdigest()==source['nrPatch']
 for rec in outcomes:assert hashlib.sha256(Path(rec['config']).read_bytes()).hexdigest()==source['configs'][rec['prefix']]
 if a.watch:
  while not (campaign/'post-complete.json').exists():time.sleep(1)
 train=np.load(campaign/'dataset-train.npz');test=np.load(campaign/'dataset-test.npz')
 train_ids=set(train['run_ids']);test_ids=set(test['run_ids']);assert not train_ids&test_ids;assert len(train_ids)==150 and len(test_ids)==50
 assert test_ids=={r['prefix'] for r in outcomes if r['replicate']==3}
 audit_summary={'completed':len(rows),'applicationCriterionPassed':sum(r['exit']==0 for r in rows),'crashes':0,'profiles':len(cases),'replicates':4,
  'kpiAccounting':'all UE volume and PRB totals reconciled','udpTcpFields':'all NaN','trainRuns':len(train_ids),'testRuns':len(test_ids),
  'trainWindows':len(train['sequences']),'testWindows':len(test['sequences']),'features':len(train['feature_names']),'radioFeatures':len(RADIO_FEATURES),'auditedMetricRunPairs':len(audit),
  'maxUlSlotPhaseErrorNs':max(r['max_ul_slot_arrival_error_ns'] for r in rows),'maxBoundaryOverlapNs':max(r['max_boundary_overlap_ns'] for r in rows),'minElevationDeg':min(r['min_elevation_deg'] for r in rows)}
 (campaign/'validation.json').write_text(json.dumps(audit_summary,indent=2)+'\n')
 result={'validation':audit_summary,'caseMedians':{name:{key:med(name,key) for key in ('ul_goodput_kbps','dl_goodput_kbps','srtt_ms','srtt_p90_ms','ul_sinr_db','ul_tb_fail_pct','dl_sdu_delay_ms','propagation_rtt_ms','primary_movement_m','background_goodput_kbps','ul_mcs')} for name in cases}}
 (campaign/'comparison-summary.json').write_text(json.dumps(clean(result),indent=2)+'\n');print(json.dumps(audit_summary,indent=2))
if __name__=='__main__':main()
