"""Read-only BM25, legacy inventory, ROUGE and lineage checks."""
import json,math,re,hashlib,statistics as st,subprocess
from pathlib import Path
from collections import Counter
R=Path(__file__).resolve().parents[1];O=Path(__file__).resolve().parent
def load(p):return json.loads((R/p).read_text(encoding='utf-8'))
def dump(n,x):(O/n).write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding='utf-8')
run=load('evaluations/CAMERA_READY_OFFLINE_20260905_204533/run.json');train=load('splits/10cat/train.json')
docs=[f"{x['category']} {x['question']} {x['answer']}".lower().split() for x in train];freq=[Counter(d) for d in docs];df=Counter(w for d in docs for w in set(d));n=len(docs);avg=st.mean(map(len,docs))
idf={w:math.log(n-c+.5)-math.log(c+.5) for w,c in df.items()};epsilon=.25*st.mean(idf.values());idf={w:max(v,epsilon) if v<0 else v for w,v in idf.items()}
matches=[]
for cfg in ['F_RAG_BM25','G_BASE_RAG']:
    for q in run['variants'][cfg]['answers']:
        if not q['meta']['bm25_fired']:continue
        toks=q['question'].lower().split();sc=[sum(idf.get(w,0)*f.get(w,0)*2.5/(f.get(w,0)+1.5*(.25+.75*len(docs[i])/avg)) for w in toks) for i,f in enumerate(freq)]
        best=max(range(n),key=sc.__getitem__);m=q['meta']
        matches.append(dict(config=cfg,qid=q['question_id'],train_index=best,retrieved_question=train[best]['question'],score=round(sc[best],4),recorded_score=m['retrieved_score'],question_matches=train[best]['question'][:80]==m['retrieved_question'],score_matches=round(sc[best],4)==m['retrieved_score']))
dump('retrieval_reconstruction.json',matches)
def rouge(a,b):
    a=a.lower().split();b=b.lower().split();prev=[0]*(len(b)+1)
    for x in a:
        now=[0]
        for j,y in enumerate(b):now.append(prev[j]+1 if x==y else max(now[-1],prev[j+1]))
        prev=now
    return round(2*prev[-1]/(len(a)+len(b)),4) if a or b else 0
metrics=load('evaluations/CAMERA_READY_OFFLINE_20260905_204533/metrics.json');recomputed={}
for cfg,v in run['variants'].items():
    qs=v['answers'];vals=[rouge(q['answer'],q['reference']) for q in qs]
    recomputed[cfg]=dict(mean=round(st.mean(vals),4),reported=metrics[cfg]['rougeL_mean'],n_at_token_cap=sum(q['tokens_generated']==350 for q in qs),mean_words=st.mean(len(q['answer'].split()) for q in qs))
dump('rouge_check.json',recomputed)
data=(R/'splits/10cat/train.json').read_bytes();crlf=data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
def artifact(data):return hashlib.sha256(b'train.json\0'+str(len(data)).encode()+b'\0'+data).hexdigest()
dump('train_hash_relocation.json',dict(current=artifact(data),CRLF=artifact(crlf),recorded=run['run_args']['_artifacts']['train_split']['sha256'],same_with_CRLF=artifact(crlf)==run['run_args']['_artifacts']['train_split']['sha256']))
old=[]
for p in sorted((R/'evaluations').glob('*/run.json')):
    r=json.loads(p.read_text(encoding='utf-8'));entry=dict(path=p.relative_to(R).as_posix(),timestamp=r.get('timestamp',r.get('run_at')),metadata=r.get('meta',r.get('run_args')),variants={})
    if 'variants'in r:entry['variants']={k:dict(n=len(v['answers'])) for k,v in r['variants'].items()}
    else:
        for v in r.get('results',[]):
            if not isinstance(v,dict):continue
            name=v.get('variant_key',v.get('model_key',v.get('model',v.get('variant','Unknown'))))
            entry['variants'][str(name)]={k:x for k,x in v.items() if k!='answers'}
            entry['variants'][str(name)]['n_answers']=len(v.get('answers',[]))
    old.append(entry)
dump('all_experiments.json',old)
print(json.dumps(dict(retrieval_matches=sum(x['question_matches'] and x['score_matches'] for x in matches),retrieval_total=len(matches),rouge=recomputed,train_hash_matches_CRLF=artifact(crlf)==run['run_args']['_artifacts']['train_split']['sha256']),indent=2))
