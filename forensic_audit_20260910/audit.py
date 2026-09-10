"""Independent, read-only research-artifact verification. Stdlib only; no API calls.
Run: python forensic_audit_20260910/audit.py
Writes only beneath this script's directory. Does not import study aggregation.
"""
import json, csv, hashlib, re, random, math, statistics as st, itertools, subprocess
from pathlib import Path
from collections import Counter, defaultdict
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(__file__).resolve().parent
def js(p): return json.loads((ROOT/p).read_text(encoding='utf-8'))
def jl(p): return [json.loads(x) for x in (ROOT/p).read_text(encoding='utf-8').splitlines() if x.strip()]
def csvrows(p): return list(csv.DictReader((ROOT/p).open(encoding='utf-8',newline='')))
def dump(name,data): (OUT/name).write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
def writecsv(name,rows):
    if not rows:return
    with (OUT/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def norm(t):return ' '.join(re.findall(r'\w+',t.lower()))
def ci(ds):
    rng=random.Random(2026);n=len(ds)
    vals=sorted(sum(ds[rng.randrange(n)] for _ in range(n))/n for _ in range(10000))
    return vals[250],vals[9750]
def contrast(ds):
    w=sum(d>0 for d in ds);l=sum(d<0 for d in ds);n=w+l
    p=min(1,2*sum(math.comb(n,k) for k in range(min(w,l)+1))/2**n) if n else 1
    lo,hi=ci(ds);sd=st.stdev(ds) if len(ds)>1 else 0
    return dict(n=len(ds),mean=st.mean(ds),median=st.median(ds),ci_lo=lo,ci_hi=hi,dz=st.mean(ds)/sd if sd else None,wins=w,losses=l,ties=len(ds)-n,p=p,min_p_at_observed_ties=min(1,2/2**n))
bank=js('evaluations/eval_bank_v2_40q/eval_bank_v2.json');bankidx={x['question_id']:x for x in bank}
blind=js('judging/blind_map.json');items=jl('judging/items.jsonl');itemidx={(x['qid'],x['blind_id']):x for x in items}
run=js('evaluations/CAMERA_READY_OFFLINE_20260905_204533/run.json')
dump('generation_metadata.json',{k:v for k,v in run.items() if k!='variants'})
summary={};comparisons=[];categories=[];itemrows=[];errors=[];controlrows=[];Q={};S={}
judges=['deepseek','claude_or','gpt_ar','glm_ar']
for judge in judges:
    base=f'judging/results/{judge}/OFFLINE_FINAL/'
    raw=jl(base+'judgments.jsonl');q={};s={}
    for x in raw:
        key=(x['qid'],blind[x['blind_id']])
        (q if x['prompt_type']=='quality' else s)[key]=x['parsed']
    Q[judge]=q;S[judge]=s
    summary[judge]={'n_rows':len(raw),'n_unique':len({(x['qid'],x['blind_id'],x['prompt_type']) for x in raw}),'status':dict(Counter(x['status'] for x in raw)),'returned_models':dict(Counter(x.get('model_returned') for x in raw)),'time_range':[min(x['judged_at'] for x in raw),max(x['judged_at'] for x in raw)],'cache_hits':sum(x.get('cache_hit',False) for x in raw),'reasoning_tokens':dict(Counter(str((x.get('usage') or {}).get('reasoning_tokens','not recorded')) for x in raw)),'configs':{},'high_quality_with_safety_flags':[]}
    for cfg in sorted({k[1] for k in q if not k[1].startswith('CTRL_')}):
        vals=[q[(qid,cfg)]['score'] for qid in sorted(bankidx)]
        sc=[q[(qid,cfg)]['score'] for qid in sorted(bankidx) if bankidx[qid]['safety_critical']]
        non=[q[(qid,cfg)]['score'] for qid in sorted(bankidx) if not bankidx[qid]['safety_critical']]
        danger=sum(any(s[(qid,cfg)]['violations'].values()) for qid in bankidx)
        summary[judge]['configs'][cfg]=dict(n=len(vals),mean=st.mean(vals),SC=st.mean(sc),nonSC=st.mean(non),danger=danger)
        for qid in sorted(bankidx):
            flags=[k for k,v in s[(qid,cfg)]['violations'].items() if v]
            score=q[(qid,cfg)]['score']
            itemrows.append(dict(judge=judge,qid=qid,config=cfg,category=bankidx[qid]['category'],SC=bankidx[qid]['safety_critical'],score=score,flags=';'.join(flags),rationale=q[(qid,cfg)].get('rationale','')))
            if flags and score>=2:summary[judge]['high_quality_with_safety_flags'].append(dict(qid=qid,config=cfg,score=score,flags=flags))
        reported=next(r for r in csvrows(base+'config_summary.csv') if r['config']==cfg)
        for field,val in [('overall_mean',st.mean(vals)),('sc_mean',st.mean(sc)),('nonsc_mean',st.mean(non)),('danger_any',danger)]:
            if abs(float(reported[field])-round(val,4))>1e-9:errors.append(dict(judge=judge,config=cfg,field=field,reported=reported[field],computed=val))
    for r in csvrows(base+'scores_per_question.csv'):
        if q[(r['qid'],r['config'])]['score']!=int(r['quality_score']):errors.append(dict(judge=judge,per_item_mismatch=r))
    for r in csvrows(base+'stats.csv'):
        ds=[q[(qid,r['cfg_a'])]['score']-q[(qid,r['cfg_b'])]['score'] for qid in sorted(bankidx) if r['filter']=='all' or bankidx[qid]['safety_critical']]
        c=contrast(ds);comparisons.append(dict(judge=judge,name=r['name'],**c))
        for field,key,dec in [('mean_delta','mean',4),('ci_lo','ci_lo',4),('ci_hi','ci_hi',4),('sign_p','p',6),('wins','wins',0),('losses','losses',0),('ties','ties',0)]:
            if float(r[field])!=round(c[key],dec):errors.append(dict(judge=judge,contrast=r['name'],field=field,reported=r[field],computed=c[key]))
    for ctrl in js('judging/controls_key.json'):
        key=(ctrl['qid'],ctrl['control']);score=q[key]['score'];lo,hi=ctrl['expected_score_range'];planted=ctrl.get('planted_override_id');flagged=not planted or s[key]['violations'][planted]
        controlrows.append(dict(judge=judge,control=key[1],qid=key[0],score=score,lo=lo,hi=hi,planted=planted,flagged=flagged,passed=lo<=score<=hi and flagged))
    for cat in sorted({b['category'] for b in bank}):
        ids=[b['question_id'] for b in bank if b['category']==cat];means={c:st.mean(q[(qid,c)]['score'] for qid in ids) for c in summary[judge]['configs']}
        d=contrast([q[(qid,'B_FINETUNED_4BIT')]['score']-q[(qid,'A_BASE_4BIT')]['score'] for qid in ids])
        categories.append(dict(judge=judge,category=cat,n=len(ids),A=means['A_BASE_4BIT'],B=means['B_FINETUNED_4BIT'],best=max(means,key=means.get),best_mean=max(means.values()),delta=d['mean'],ci_lo=d['ci_lo'],ci_hi=d['ci_hi'],danger_B=sum(any(s[(qid,'B_FINETUNED_4BIT')]['violations'].values()) for qid in ids)))
    for filt in ['sc','nonsc']:
        ds=[q[(qid,'B_FINETUNED_4BIT')]['score']-q[(qid,'A_BASE_4BIT')]['score'] for qid in sorted(bankidx) if bool(bankidx[qid]['safety_critical'])==(filt=='sc')]
        comparisons.append(dict(judge=judge,name='B-A '+filt,**contrast(ds)))
    # Holm adjustment across the eight stored comparisons (explicit audit sensitivity).
    cs=[c for c in comparisons if c['judge']==judge and c['name'] not in ['B-A sc','B-A nonsc']];running=0
    for rank,c in enumerate(sorted(cs,key=lambda c:c['p'])):
        running=max(running,min(1,(len(cs)-rank)*c['p']));c['holm8_p']=running
    for c in comparisons:
        if c['judge']==judge:c.setdefault('holm8_p',None)
writecsv('contrasts.csv',comparisons);writecsv('categories.csv',categories);writecsv('per_item.csv',itemrows);writecsv('controls.csv',controlrows)
dump('raw_verification.json',summary);dump('numeric_mismatches.json',errors)
# Same-output placebo: measure independent rejudging of text-identical arms.
identical=[]
ans={cfg:{x['question_id']:x for x in v['answers']} for cfg,v in run['variants'].items()}
for a,b in [('B_FINETUNED_4BIT','D_T4_IMPROVED'),('B_FINETUNED_4BIT','E_T6_IMPROVED'),('B_FINETUNED_4BIT','F_RAG_BM25')]:
    ids=[qid for qid in bankidx if ans[a][qid]['answer']==ans[b][qid]['answer']]
    identical.append(dict(a=a,b=b,n_identical=len(ids),judge_disagreements={j:dict(n=sum(Q[j][(qid,a)]['score']!=Q[j][(qid,b)]['score'] for qid in ids),mean_delta=st.mean(Q[j][(qid,b)]['score']-Q[j][(qid,a)]['score'] for qid in ids) if ids else None,max_absolute=max([abs(Q[j][(qid,b)]['score']-Q[j][(qid,a)]['score']) for qid in ids],default=0)) for j in judges}))
dump('identical_answer_judging.json',identical)
dump('interventions.json',{cfg:dict(n=len(v),meta_counts={field:dict(Counter(str(x.get('meta',{}).get(field)) for x in v.values())) for field in ['retried','flagged_unsafe','gate_verdict','bm25_fired','bm25_skipped_gap','gap_topic']},truncated=sum(x.get('repetition_truncated',False) for x in v.values()),empty=sum(not x['answer'].strip() for x in v.values())) for cfg,v in ans.items()})
# Generation-to-judge equality, frozen metadata, and historical versions.
lineage=[]
for path in sorted((ROOT/'evaluations').glob('*/run.json')):
    rr=json.loads(path.read_text(encoding='utf-8'));vv=rr.get('variants',{})
    lineage.append(dict(path=str(path.relative_to(ROOT)),run_at=rr.get('run_at'),args=rr.get('run_args'),counts={k:len(v.get('answers',[])) for k,v in vv.items()}))
dump('run_inventory.json',lineage)
real=[x for x in items if not x['config'].startswith('CTRL_')]
dump('lineage_checks.json',dict(real_items=len(real),answer_mismatches=[(x['qid'],x['config']) for x in real if x['answer']!=ans[x['config']][x['qid']]['answer']],reference_mismatches=[(x['qid'],x['config']) for x in real if x['reference']!=bankidx[x['qid']]['reference']],standalone_mismatches=[cfg for cfg in ans if js('evaluations/CAMERA_READY_OFFLINE_20260905_204533/'+cfg+'.json')['answers']!=run['variants'][cfg]['answers']]))
# Dataset audit: exact normalized questions, exact answer reuse, token Jaccard candidates.
datasets={p.relative_to(ROOT).as_posix():json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'splits').glob('*/*.json')}
datasets['bank']=bank
data_summary={k:dict(n=len(v),unique_questions=len({norm(x['question']) for x in v}),unique_answers=len({norm(x.get('answer',x.get('reference',''))) for x in v}),categories=dict(Counter(x.get('category') for x in v)),SC=sum(bool(x.get('safety_critical')) for x in v)) for k,v in datasets.items()}
dump('dataset_inventory.json',data_summary)
overlaps=[];near=[]
for family in ['10cat','baseline','thresh020']:
    for a,b in [('train','val'),('train','test'),('val','test')]:
        aa=datasets[f'splits/{family}/{a}.json'];bb=datasets[f'splits/{family}/{b}.json']
        aq=Counter(norm(x['question']) for x in aa);bq=Counter(norm(x['question']) for x in bb)
        ac=Counter(norm(x['answer']) for x in aa);bc=Counter(norm(x['answer']) for x in bb)
        overlaps.append(dict(family=family,a=a,b=b,shared_questions=len(aq.keys()&bq.keys()),b_question_hits=sum(bq[k] for k in aq.keys()&bq.keys()),shared_answers=len(ac.keys()&bc.keys()),examples=list(sorted(aq.keys()&bq.keys()))[:12]))
train=datasets['splits/10cat/train.json']
trainsets=[set(norm(x['question']).split()) for x in train]
for label,queries in [('bank',bank),('val',datasets['splits/10cat/val.json']),('test',datasets['splits/10cat/test.json'])]:
    for qi,x in enumerate(queries):
        toks=set(norm(x['question']).split());scores=[len(toks&t)/len(toks|t) if toks|t else 1 for t in trainsets];idx=max(range(len(scores)),key=scores.__getitem__)
        if label=='bank' or scores[idx]>=.75:
            near.append(dict(split=label,index=qi,qid=x.get('question_id'),question=x['question'],jaccard=scores[idx],train_index=idx,train_question=train[idx]['question'],train_answer=train[idx]['answer'],reference=x.get('reference',x.get('answer'))))
writecsv('near_duplicates.csv',near);dump('split_overlap.json',overlaps)
# Record every file's size and hash, excluding credentials, git database, generated audit, caches.
inventory=[]
for p in sorted(ROOT.rglob('*')):
    if not p.is_file() or any(x in p.relative_to(ROOT).parts for x in ['.git','__pycache__','forensic_audit_20260910']) or p.name.startswith('.env'):continue
    inventory.append(dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
writecsv('file_inventory.csv',inventory)
training=[]
for p in sorted((ROOT/'experiments').glob('*/training_curve.json')):
    x=json.loads(p.read_text(encoding='utf-8'));training.append(dict(path=str(p.relative_to(ROOT)),**{k:v for k,v in x.items() if k!='epoch_losses'}))
dump('training_inventory.json',training)
print(json.dumps(dict(numeric_mismatches=len(errors),judges={j:summary[j]['n_rows'] for j in judges},controls={j:sum(r['passed'] for r in controlrows if r['judge']==j) for j in judges},identical=identical,files=len(inventory)),indent=2))
