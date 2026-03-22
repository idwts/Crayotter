PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' \
USE_AUDIO_IN_VIDEO=1 \
NPROC_PER_NODE=8 \
CUDA_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 \
FPS_MAX_FRAMES=12 \
megatron sft \
    --model /data/liwenxi/agent/GenVideo/Qwen3-Omni-30B-A3B-Instruct \
    --save_safetensors true \
    --merge_lora false \
    --dataset /data/liwenxi/agent/GenVideo/data/video_mme_train_1k.jsonl \
    --load_from_cache_file true \
    --train_type lora \
    --lora_rank 2 \
    --lora_alpha 4 \
    --target_modules all-linear \
    --sequence_parallel true \
    --freeze_llm false \
    --freeze_vit true \
    --freeze_aligner true \
    --packing true \
    --split_dataset_ratio 0.01 \
    --tensor_model_parallel_size 1 \
    --expert_model_parallel_size 8 \
    --optimizer_cpu_offload true \
    --use_precision_aware_optimizer true \
    --cpu_offloading true \
    --cpu_offloading_num_layers 24 \
    --cpu_offloading_activations true \
    --cpu_offloading_weights true \
    --cpu_offloading_double_buffering true \
    --moe_permute_fusion true \
    --moe_grouped_gemm true \
    --moe_shared_expert_overlap true \
    --moe_aux_loss_coeff 1e-3 \
    --micro_batch_size 1 \
    --global_batch_size 8 \
    --recompute_granularity full \
    --recompute_method uniform \
    --recompute_num_layers 1 \
    --finetune true \
    --cross_entropy_loss_fusion true \
    --lr 1e-4 \
    --lr_warmup_fraction 0.05 \
    --min_lr 1e-5 \
    --max_epochs 1 \
    --save /data/liwenxi/agent/GenVideo/megatron_output/Qwen3-Video-MME \
    --eval_interval 200 \
    --save_interval 200 \
    --max_length 4096 \
    --num_workers 8 \
    --dataset_num_proc 16 \
    --no_save_optim true \
    --no_save_rng true \
    --attention_backend flash