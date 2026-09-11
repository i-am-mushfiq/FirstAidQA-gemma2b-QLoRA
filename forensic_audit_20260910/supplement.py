"""Supplementary read-only provenance, reliability and sensitivity audit."""
import json,csv,re,hashlib,statistics as st,itertools,ast,subprocess,sys,math,random,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
from collections import Counter,defaultdict
R=Path(__file__).resolve().parents[1];O=Path(__file__).resolve().parent
def load(p):return json.loads((R/p).read_text(encoding='utf-8'))
def lines(p):return [json.loads(s) for s in (R/p).read_text(encoding='utf-8').splitlines() if s]
def dump(p,x):(O/p).write_text(json.dumps(x,indent=2,ensure_ascii=False),encoding='utf-8')
def sha(x):return hashlib.sha256(x.encode()).hexdigest()
def norm(s):return ' '.join(re.findall(r'\w+',s.lower()))
def corr(a,b):
    ma=st.mean(a);mb=st.mean(b);da=[v-ma for v in a];db=[v-mb for v in b]
    den=math.sqrt(sum(v*v for v in da)*sum(v*v for v in db))
    return sum(x*y for x,y in zip(da,db))/den if den else None
def ranks(a):
    ix=defaultdict(list)
    for i,v in enumerate(sorted(a)):ix[v].append(i+1)
    return [st.mean(ix[v]) for v in a]
bank=load('evaluations/eval_bank_v2_40q/eval_bank_v2.json');bi={q['question_id']:q for q in bank}
items=lines('judging/items.jsonl');ii={(x['qid'],x['blind_id']):x for x in items};blind=load('judging/blind_map.json')
run=load('evaluations/CAMERA_READY_OFFLINE_20260905_204533/run.json');ans={(x['question_id'],c):x for c,v in run['variants'].items() for x in v['answers']}
js=['deepseek','claude_or','gpt_ar','glm_ar'];Q={};S={};check={};safety=[];cats=[]
qt=(R/'judging/prompt_quality.txt').read_text(encoding='utf-8');sp=(R/'judging/prompt_safety.txt').read_text(encoding='utf-8')
for j in js:
    raw=lines(f'judging/results/{j}/OFFLINE_FINAL/judgments.jsonl');m=load(f'judging/results/{j}/OFFLINE_FINAL/manifest.json')
    Q[j]={(x['qid'],blind[x['blind_id']]):x['parsed']['score'] for x in raw if x['prompt_type']=='quality'}
    S[j]={(x['qid'],blind[x['blind_id']]):x['parsed']['violations'] for x in raw if x['prompt_type']=='safety'}
    bad=[]
    for x in raw:
        it=ii[(x['qid'],x['blind_id'])];t=qt if x['prompt_type']=='quality' else sp
        prompt=t.replace('{question}',it['question']).replace('{sc_flag}',str(it['sc_flag'])).replace('{reference}',it['reference']).replace('{answer}',it['answer'])
        # Follow the recorded canonical cache identity, not the aggregation implementation.
        nonce=m.get('nonce') or ''
        text=f"{m['model_requested']}|{m['template_hash']}|{x['prompt_type']}|{x['qid']}|{x['blind_id']}|{sha(it['answer'])}|{sha(prompt)}|{nonce}|{m['decode_fingerprint']}"
        if sha(text)!=x['cache_key']:bad.append((x['qid'],blind[x['blind_id']],x['prompt_type']))
    check[j]=dict(cache_key_mismatches=bad,quality_hash_matches=sha(qt)==m['quality_hash'],safety_hash_matches=sha(sp)==m['safety_hash'])
    keys=sorted(ans)
    lens=[len(ans[k]['answer']) for k in keys];scores=[Q[j][k] for k in keys]
    check[j]['length_spearman']=corr(ranks(lens),ranks(scores))
    # Reference-score agreement is not clinical sensitivity; this reconciles lanes only.
    check[j]['safety_quality_conflicts']=sum(Q[j][k]>=2 and any(S[j][k].values()) for k in keys)
    check[j]['score_ge4_flagged']=sum(Q[j][k]>=4 and any(S[j][k].values()) for k in keys)
    for cfg in run['variants']:
        kk=[k for k in keys if k[1]==cfg];sc=[k for k in kk if bi[k[0]]['safety_critical']]
        safety.append(dict(judge=j,config=cfg,flagged=sum(any(S[j][k].values()) for k in kk),flagged_SC=sum(any(S[j][k].values()) for k in sc),quality_le1=sum(Q[j][k]<=1 for k in kk),quality_ge4=sum(Q[j][k]>=4 for k in kk)))
    base='B_FINETUNED_4BIT';gate='E_T6_IMPROVED'
    flaggedids=[qid for qid in bi if ans[(qid,gate)]['meta']['flagged_unsafe']]
    dangerousids=[qid for qid in bi if any(S[j][(qid,base)].values())]
    check[j]['gate_vs_external_safety']=dict(n_gate=len(flaggedids),n_judge_flags=len(dangerousids),intersection=sorted(set(flaggedids)&set(dangerousids)),gate_only=sorted(set(flaggedids)-set(dangerousids)),missed=sorted(set(dangerousids)-set(flaggedids)))
    # Safety-override reconciliation sensitivity, not a replacement clinical gold standard.
    for a,b in [('B_FINETUNED_4BIT','A_BASE_4BIT'),('F_RAG_BM25','B_FINETUNED_4BIT')]:
        ds=[min(Q[j][(qid,a)],1) if any(S[j][(qid,a)].values()) else Q[j][(qid,a)] for qid in sorted(bi)]
        bs=[min(Q[j][(qid,b)],1) if any(S[j][(qid,b)].values()) else Q[j][(qid,b)] for qid in sorted(bi)]
        check[j][a+'-'+b+'_safety_capped_delta']=st.mean(x-y for x,y in zip(ds,bs))
agreement=[]
for a,b in itertools.combinations(js,2):
    keys=sorted(ans);qa=[Q[a][k] for k in keys];qb=[Q[b][k] for k in keys];aa=Counter(qa);bb=Counter(qb);n=len(keys)
    po=sum(x==y for x,y in zip(qa,qb))/n;pe=sum(aa[i]*bb[i]/n**2 for i in range(6))
    sa=[any(S[a][k].values()) for k in keys];sb=[any(S[b][k].values()) for k in keys]
    agreement.append(dict(a=a,b=b,n=n,exact=po,kappa=(po-pe)/(1-pe),spearman=corr(ranks(qa),ranks(qb)),safety_agreement=sum(x==y for x,y in zip(sa,sb))/n,BA_delta_correlation=corr([Q[a][(qid,'B_FINETUNED_4BIT')]-Q[a][(qid,'A_BASE_4BIT')] for qid in bi],[Q[b][(qid,'B_FINETUNED_4BIT')]-Q[b][(qid,'A_BASE_4BIT')] for qid in bi])))
dump('supplementary_checks.json',check);dump('judge_agreement.json',agreement);dump('safety_counts.json',safety)
# Pool judgments descriptively within question; do not pretend 123 questions.
pooled={k:st.mean(Q[j][k] for j in js[:3]) for k in ans}
for cat in sorted({q['category'] for q in bank}):
    ids=[q['question_id'] for q in bank if q['category']==cat]
    means={cfg:st.mean(pooled[(qid,cfg)] for qid in ids) for cfg in run['variants']}
    best=max(means,key=means.get)
    cats.append(dict(category=cat,n=len(ids),A=means['A_BASE_4BIT'],B=means['B_FINETUNED_4BIT'],best=best,best_mean=means[best],BA=means['B_FINETUNED_4BIT']-means['A_BASE_4BIT']))
dump('pooled_categories.json',cats)
# Exact bank overlap, overlap to raw dataset, deterministic split reconstruction.
sys.path.insert(0,str(R));import data_v2
datasetcheck={};train=load('splits/10cat/train.json')
for label,path in [('train','splits/10cat/train.json'),('val','splits/10cat/val.json'),('test','splits/10cat/test.json'),('raw','data/firstaidqa_v1.json')]:
    data=load(path);qq={norm(x['question']) for x in data};aa={norm(x['answer']) for x in data}
    datasetcheck[label]=dict(bank_exact_q=[x['question_id'] for x in bank if norm(x['question']) in qq],bank_exact_ref=[x['question_id'] for x in bank if norm(x['reference']) in aa])
e=load('data/firstaidqa_v1_enriched_10cat.json');ss=data_v2.stratified_split(e)
datasetcheck['split_reconstruction']={label:part==load(f'splits/10cat/{label}.json') for label,part in zip(['train','val','test'],ss)}
near=list(csv.DictReader((O/'near_duplicates.csv').open(encoding='utf-8')))
datasetcheck['near_candidate_counts']={label:{str(th):sum(x['split']==label and float(x['jaccard'])>=th for x in near) for th in [.75,.85,.95,1]} for label in ['bank','val','test']}
datasetcheck['bank_top_near']=sorted([x for x in near if x['split']=='bank'],key=lambda x:float(x['jaccard']),reverse=True)[:8]
dump('dataset_supplement.json',datasetcheck)
# Read all source files; index imports/entrypoints to support complete system map.
source=[]
for p in sorted(R.rglob('*.py')):
    if any(x in p.parts for x in ['.git','forensic_audit_20260910','__pycache__']):continue
    text=p.read_text(encoding='utf-8-sig')
    try:
        tree=ast.parse(text);defs=[dict(name=n.name,line=n.lineno) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
        source.append(dict(path=p.relative_to(R).as_posix(),lines=len(text.splitlines()),definitions=defs))
    except SyntaxError as ex:source.append(dict(path=str(p),syntax_error=str(ex)))
dump('source_map.json',source)
# Narrative material is indexed after independent results, never treated as evidence of correctness.
for p in R.rglob('*.docx'):
    with zipfile.ZipFile(p) as z:
        root=ET.fromstring(z.read('word/document.xml'));text='\n'.join(''.join(n.itertext()) for n in root.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
        (O/(p.stem+'_extracted.txt')).write_text(text,encoding='utf-8')
# Compact paired answers for direct reviewer inspection, all 287 outputs and 41 references.
parts=[]
for qid,q in bi.items():
    parts.extend([f'## {qid}: {q["question"]}',f'SC={q["safety_critical"]}; category={q["category"]}',f'Reference: {q["reference"]}'])
    for cfg in sorted(run['variants']):
        k=(qid,cfg);parts.extend([f'### {cfg} scores {[Q[j][k] for j in js]} flags {[";".join(c for c,v in S[j][k].items() if v) for j in js]}',ans[k]['answer']])
(O/'output_dossier.md').write_text('\n\n'.join(parts),encoding='utf-8')
print(json.dumps(dict(source_files=len(source),checks=check,dataset=datasetcheck,agreement=agreement),indent=2))
