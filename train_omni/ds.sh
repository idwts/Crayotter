#!/bin/bash

export PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True,max_split_size_mb:512'
export USE_AUDIO_IN_VIDEO=1
# export FPS=2.0                    # swift 用环境变量控制 fps
export FPS_MAX_FRAMES=12           # 环境变量
export OMP_NUM_THREADS=4
USE_AUDIO_IN_VIDEO=1

DEEPSPEED_CONFIG="/data/liwenxi/agent/GenVideo/ds_config_zero3_offload.json"

python3 -m torch.distributed.run \
    --nproc_per_node 8 \
    /data/liwenxi/miniconda3/envs/swift/lib/python3.12/site-packages/swift/cli/sft.py \
    --model /data/liwenxi/agent/GenVideo/Qwen3-Omni-30B-A3B-Instruct \
    --dataset /data/liwenxi/agent/GenVideo/data/video_mme_train_1k.jsonl \
    --deepspeed $DEEPSPEED_CONFIG \
    --train_type lora \
    --lora_rank 8 \
    --lora_alpha 32 \
    --target_modules all-linear \
    --freeze_vit true \
    --freeze_aligner true \
    --max_length 4096 \
    --per_device_train_batch_size 1 \
    --num_train_epochs 1 \
    --output_dir /data/liwenxi/agent/GenVideo/deepspeed_output/Qwen3-Video-MME \
    --eval_steps 200 \
    --save_steps 200 \
    --learning_rate 1e-4 \
    --warmup_ratio 0.05 \
    --weight_decay 0.1 \
    --gradient_accumulation_steps 1 \
    --max_grad_norm 1.0 \
    --seed 42 \
    --save_safetensors true \
    --load_from_cache_file true \
    --dataset_num_proc 16 \
    --dataloader_num_workers 4 \
    --dataloader_pin_memory true
    # 删除: --merge_lora false（swift 不支持）