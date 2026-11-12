import json,sys,collections
sys.path.insert(0,'.')
from src.datamate.grading import is_correct
G={json.loads(l)['id']:json.loads(l) for l in open('dataset/zaloAI/gt-sonnet/submission.jsonl')}
GT={json.loads(l)['id']:json.loads(l) for l in open('dataset/zaloAI/gt-sonnet/traces.jsonl')}
Q={json.loads(l)['id']:json.loads(l) for l in open('runs/public_test_qwen9b_final/traces.jsonl')}
cat=collections.Counter(); rows=[]
for i,g in G.items():
    q=Q[i]['submission']; ga=g['type']=='abstain'; qa=q['type']=='abstain'
    if ga and qa: c='both abstain'
    elif ga: c='sonnet abstain, 9B answered'
    elif qa: c='9B abstain, sonnet answered'
    else: c='agree' if is_correct(q,{**g,'alias':[]}) else 'disagree'
    cat[c]+=1; rows.append((i,c))
print(dict(cat))
print('sonnet types',dict(collections.Counter(g['type'] for g in G.values())))
json.dump(rows,open(sys.argv[1],'w'))
# disagreement by type
d=collections.Counter((G[i]['type'],Q[i]['submission']['type']) for i,c in rows if c=='disagree'); print('disagree (sonnet type, 9B type):',d.most_common(12))
