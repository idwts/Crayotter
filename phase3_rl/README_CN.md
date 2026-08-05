# Crayotter Phase 3 RL

这是可复现的工具调用视频剪辑训练代码与 GRPB credit allocator。目录不包含权重、素材、日志、API 密钥或服务器路径。

```bash
conda env create -f environment.yml
conda activate crayotter-phase3
git clone https://github.com/volcengine/verl.git _vendor/verl
git -C _vendor/verl checkout 8a694930275061f52ebd538c906ef8819af56dbd
pip install -e _vendor/verl
```

复制并修改 `configs/grpb.env.example`，然后运行 `bash train.sh configs/grpb.env`。数据和工具配置分别由 `export_verl_phase3_dataset.py`、`generate_verl_tool_config.py` 生成。测试命令：

```bash
python -m unittest discover -s tests -v
```
