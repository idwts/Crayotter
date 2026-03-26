from pathlib import Path
from tensorboard.backend.event_processing import event_accumulator
import pandas as pd

data = []
for f in Path('./').rglob('events.out.tfevents.*'):
    try:
        ea = event_accumulator.EventAccumulator(str(f.parent))
        ea.Reload()
        for tag in ea.Tags().get('scalars', []):
            for e in ea.Scalars(tag):
                data.append({ 'tag': tag, 'step': e.step, 'value': e.value})
    except: pass

df = pd.DataFrame(data)
df.to_csv('tb_output.csv', index=False)
print(f'导出 {len(df)} 行到 tb_output.csv')