MASTER_PORT=29501 \
CUDA_VISIBLE_DEVICES=4,5,6,7 \
NPROC_PER_NODE=4 \
PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' \
megatron rlhf \
    --rlhf_type grpo \
    --model unsloth/gpt-oss-20b-BF16 \
    --train_type lora \
    --quant_bits 4 \
    --bnb_4bit_quant_type nf4 \
    --lora_rank 8 \
    --lora_alpha 16 \
    --target_modules all-linear \
    --save_safetensors true \
    --merge_lora false \
    --context_parallel_size 1 \
    --tensor_model_parallel_size 4 \
    --expert_model_parallel_size 1 \
    --pipeline_model_parallel_size 1 \
    --dataset open-r1/DAPO-Math-17k-Processed \
    --num_train_epochs 1 \
    --global_batch_size 4 \
    --micro_batch_size 1 \
    --steps_per_generation 2 \
    --num_generations 4 \
    --reward_funcs accuracy format \
    --use_vllm true \
    --vllm_mode server \
    --vllm_server_host localhost \
    --vllm_server_port 8000 \
    --max_length 8192 \
    --max_completion_length 8192 \
    --tuner_type lora \
    --lr 5e-5 \
    --bf16 true \
    --beta 0.00 \
    --importance_sampling_level sequence \
    --epsilon 3e-4 \
    --epsilon_high 4e-4 \
    --dynamic_sample false \
    --overlong_filter true \
    --loss_type grpo \
    --offload_bridge true \
    --logging_steps 1 \
    --recompute_granularity full \
    --recompute_method block \
    --recompute_num_layers 12 \
    --dataloader_num_workers 8 \
    --dataset_num_proc 8 \
    --no_save_optim \
    --no_save_rng \
    --attention_backend flash \
    --temperature 1.0 \
    --sequence_parallel true \
    --packing false \
    --padding_free false \
    --log_completions true \
    --report_to tensorboard

    #     --vllm_gpu_memory_utilization 0.8 \
    # --vllm_tensor_parallel_size 8 \
    # --vllm_max_model_len 16384 \
    # --vllm_max_num_seqs 1 \
    # --sleep_level 2 \
    # --offload_optimizer true \
    # --offload_model true \