DEEPSPEED_CONFIG="/data/liwenxi/agent/GenVideo/ds_config_zero3_offload.json"

CUDA_VISIBLE_DEVICES=4,5,6,7 \
NPROC_PER_NODE=4 \
MASTER_PORT=29501 \
swift rlhf \
    --rlhf_type grpo \
    --model unsloth/gpt-oss-20b-BF16 \
    --train_type lora \
    --lora_rank 8 \
    --lora_alpha 16 \
    --target_modules all-linear \
    --dataset open-r1/DAPO-Math-17k-Processed \
    --num_train_epochs 1 \
    --per_device_train_batch_size 1 \
    --gradient_accumulation_steps 8 \
    --steps_per_generation 2 \
    --num_generations 4 \
    --reward_funcs accuracy format \
    --use_vllm true \
    --vllm_mode server \
    --vllm_server_host 127.0.0.1 \
    --vllm_server_port 8000 \
    --max_length 8192 \
    --max_completion_length 8192 \
    --learning_rate 5e-5 \
    --bf16 true \
    --beta 0.00 \
    --importance_sampling_level sequence \
    --epsilon 3e-4 \
    --epsilon_high 4e-4 \
    --dynamic_sample false \
    --overlong_filter true \
    --loss_type grpo \
    --sleep_level 2 \
    --logging_steps 1 \
    --gradient_checkpointing true \
    --dataloader_num_workers 8 \
    --dataset_num_proc 8 \
    --attn_impl eager \
    --temperature 1.0 \
    --packing false \
    --log_completions true \
    --report_to tensorboard \
    --deepspeed zero3_offload