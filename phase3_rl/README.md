# Crayotter Phase 3 RL

Minimal release of the tool-using video-editing trainer and GRPB credit allocator. No checkpoints, media, logs, API keys, or server paths are included.

## Setup

```bash
conda env create -f environment.yml
conda activate crayotter-phase3
git clone https://github.com/volcengine/verl.git _vendor/verl
git -C _vendor/verl checkout 8a694930275061f52ebd538c906ef8819af56dbd
pip install -e _vendor/verl
```

Edit `configs/grpb.env.example`, copy it to `configs/grpb.env`, then run:

```bash
bash train.sh configs/grpb.env
```

Generate fixture data with `export_verl_phase3_dataset.py` and tool schemas with `generate_verl_tool_config.py`. Run tests with:

```bash
python -m unittest discover -s tests -v
```
