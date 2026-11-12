import json,re,collections,sys
G={json.loads(l)['id']:json.loads(l) for l in open('dataset/zaloAI/gt-sonnet/submission.jsonl')}
GT={json.loads(l)['id']:json.loads(l) for l in open('dataset/zaloAI/gt-sonnet/traces.jsonl')}
Q={json.loads(l)['id']:json.loads(l) for l in open('runs/public_test_qwen9b_final/traces.jsonl')}
rows=json.load(open(sys.argv[1]))
def sig(tr):
    calls=[c for m in tr['trace'] if m['role']=='ai' for c in m['tool_calls'] if c['name']=='information_finder']
    outs=[m['content'] for m in tr['trace'] if m['role']=='tool' and m.get('name')=='information_finder' and not str(m['content']).startswith(('ERROR','Error','NO DATA'))]
    rng=set(re.findall(r'(\d{4}-\d{2}-\d{2}) → (\d{4}-\d{2}-\d{2})',' '.join(outs)))
    ms={c['args'].get('measure') for c in calls}
    fl=[json.dumps(c['args'].get('filters') or {},sort_keys=True,ensure_ascii=False) for c in calls]
    gb={c['args'].get('group_by') for c in calls}
    return ms,rng,set(fl),gb,len(calls)
def fmt_date(g,q): return g['type']=='date' and q['type'] in('string','date')
cls=collections.Counter(); ex=collections.defaultdict(list)
for i,c in rows:
    if c!='disagree': continue
    g,q=G[i],Q[i]['submission']
    sm,sr,sf,sg,_=sig(GT[i]); qm,qr,qf,qg,_=sig(Q[i])
    if g['type']=='date' and q['type']!='date': k='A. sai type/format (date→string)'
    elif g['type']!=q['type']: k='B. sai answer type'
    elif not (sm & qm): k='C. sai measure'
    elif sr and not (sr & qr): k='D. sai kỳ thời gian'
    elif sg!=qg: k='E. sai group_by'
    elif not (sf & qf): k='F. sai filter/entity'
    elif g['type']=='percent' and isinstance(q.get('answer'),(int,float)) and abs(q['answer']-g['answer'])<0.05: k='G. làm tròn'
    else: k='H. cùng tool call, khác bước tính/chọn'
    cls[k]+=1; ex[k].append(i)
for k,v in sorted(cls.items()): print(f'{v:4} {k}')
json.dump(ex,open(sys.argv[2],'w'))
