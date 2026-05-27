import os
import json 
from pprint import pprint


#external 
from research_core.utils.utils import llm_as_a_judge

BASE_DIR = "/Users/eliasdzobo/Desktop/2026/scope/artifacts/BOPP"
STOCK_NAME = "Benson Oil Palm Plantation Ltd"

total = 0
trues = 0 

evaluation_response = {}


for file in os.listdir(BASE_DIR):
    with open(os.path.join(BASE_DIR, file), 'r') as f:
        data = json.load(f)
        for sample in data:
            response = llm_as_a_judge(file.split('.')[0], STOCK_NAME, sample['title'], sample['body'][:2000])
            pprint(response)
            evaluation_response[sample['title']] = {'is_relevant': response['is_relevant'], 'score': response['source_trust_score']}
            total += 1
            if response['is_relevant']:
                trues += 1
            print(sample['title'])

print(f"Total: {total}")
print(f"True: {trues}")
print(f"Accuracy: {trues/total}")
with open('artifacts/BOPP/evaluation.json', 'w') as f:
    json.dump(evaluation_response, f)