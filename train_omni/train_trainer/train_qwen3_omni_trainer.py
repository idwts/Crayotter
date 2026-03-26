#!/usr/bin/env python3
import os
import re
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import torch
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import Dataset

from transformers import Trainer, TrainingArguments, set_seed, Qwen3OmniMoeForConditionalGeneration, Qwen3OmniMoeProcessor
from peft import LoraConfig, get_peft_model

from qwen_omni_utils import process_mm_info


TAG_PATTERN = re.compile(r"<(audio|video|image)>")


def read_jsonl(path: str, max_samples: Optional[int] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
            if max_samples is not None and len(records) >= max_samples:
                break
    return records


def get_role_text(messages: List[Dict[str, Any]], role: str) -> str:
    for message in messages:
        if message.get("role") == role:
            return message.get("content", "") or ""
    return ""


def record_to_conversation(record: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str]:
    if "conversation" in record and "response" in record:
        conversation = record.get("conversation", []) or []
        response = record.get("response", "") or ""
        return conversation, response

    messages = record.get("messages", [])
    system_text = get_role_text(messages, "system")
    user_text = get_role_text(messages, "user")
    assistant_text = get_role_text(messages, "assistant")

    user_text_clean = TAG_PATTERN.sub("", user_text).strip()

    user_content: List[Dict[str, Any]] = []
    for image_path in record.get("images", []) or []:
        user_content.append({"type": "image", "image": image_path})
    for audio_path in record.get("audios", []) or []:
        user_content.append({"type": "audio", "audio": audio_path})
    for video_path in record.get("videos", []) or []:
        user_content.append({"type": "video", "video": video_path})

    if user_text_clean:
        user_content.append({"type": "text", "text": user_text_clean})
    elif not user_content:
        user_content.append({"type": "text", "text": ""})

    conversation: List[Dict[str, Any]] = []
    if system_text:
        conversation.append({"role": "system", "content": system_text})
    conversation.append({"role": "user", "content": user_content})

    return conversation, assistant_text


@dataclass
class ScriptArgs:
    model_path: str
    train_jsonl: str
    eval_jsonl: Optional[str]
    output_dir: str
    max_train_samples: Optional[int]
    max_eval_samples: Optional[int]
    use_audio_in_video: bool
    attn_implementation: str
    torch_dtype: str
    per_device_train_batch_size: int
    per_device_eval_batch_size: int
    gradient_accumulation_steps: int
    learning_rate: float
    num_train_epochs: float
    max_steps: int
    warmup_ratio: float
    weight_decay: float
    logging_steps: int
    save_steps: int
    eval_steps: int
    save_total_limit: int
    seed: int
    gradient_checkpointing: bool
    bf16: bool
    fp16: bool
    report_to: str
    use_deepspeed: bool
    use_lora: bool



class OmniSFTDataset(Dataset):
    def __init__(
        self,
        records: List[Dict[str, Any]],
        processor: Qwen3OmniMoeProcessor,
        use_audio_in_video: bool,
    ):
        self.records = records
        self.processor = processor
        self.use_audio_in_video = use_audio_in_video
        self.eos = self.processor.tokenizer.eos_token or ""

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        record = self.records[idx]
        conversation, target_text = record_to_conversation(record)

        prompt_text = self.processor.apply_chat_template(
            conversation,
            add_generation_prompt=True,
            tokenize=False,
        )

        full_text = f"{prompt_text}{target_text}{self.eos}"

        audios, images, videos = process_mm_info(
            conversation,
            use_audio_in_video=self.use_audio_in_video,
        )

        full_inputs = self.processor(
            text=full_text,
            audio=audios,
            images=images,
            videos=videos,
            return_tensors="pt",
            max_length=4096,
            padding=False,
            use_audio_in_video=self.use_audio_in_video,
        )

        prompt_inputs = self.processor(
            text=prompt_text,
            audio=audios,
            images=images,
            videos=videos,
            return_tensors="pt",
            padding=False,
            use_audio_in_video=self.use_audio_in_video,
        )

        item: Dict[str, Any] = {}
        for key, value in full_inputs.items():
            if torch.is_tensor(value):
                item[key] = value.squeeze(0)
            else:
                item[key] = value

        labels = item["input_ids"].clone()
        prompt_len = int(prompt_inputs["input_ids"].shape[1])
        labels[:prompt_len] = -100
        item["labels"] = labels

        return item


class OmniDataCollator:
    def __init__(self, processor: Qwen3OmniMoeProcessor):
        self.processor = processor
        self.pad_token_id = self.processor.tokenizer.pad_token_id
        if self.pad_token_id is None:
            self.pad_token_id = self.processor.tokenizer.eos_token_id

    def __call__(self, features: List[Dict[str, Any]]) -> Dict[str, Any]:
        input_ids = [f["input_ids"] for f in features]
        labels = [f["labels"] for f in features]

        input_ids = pad_sequence(input_ids, batch_first=True, padding_value=self.pad_token_id)
        labels = pad_sequence(labels, batch_first=True, padding_value=-100)
        attention_mask = (input_ids != self.pad_token_id).long()

        batch: Dict[str, Any] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "labels": labels,
        }

        reserved = {"input_ids", "attention_mask", "labels"}
        for key in features[0].keys():
            if key in reserved:
                continue
            values = [f[key] for f in features]
            v0 = values[0]
            if torch.is_tensor(v0):
                try:
                    batch[key] = torch.stack(values, dim=0)
                except Exception:
                    if len(values) == 1:
                        batch[key] = v0.unsqueeze(0)
                    else:
                        raise ValueError(
                            f"Cannot stack key '{key}' for batch size {len(values)}. "
                            "Use per_device_train_batch_size=1 for this data shape."
                        )
            else:
                batch[key] = values

        return batch


def parse_dtype(dtype_name: str):
    dtype_name = dtype_name.lower()
    if dtype_name == "bfloat16":
        return torch.bfloat16
    if dtype_name == "float16":
        return torch.float16
    if dtype_name == "float32":
        return torch.float32
    if dtype_name == "auto":
        return "auto"
    raise ValueError(f"Unsupported torch_dtype: {dtype_name}")

CONFIG = ScriptArgs(
    model_path="/data1/liwenxi/agent/Qwen3-Omni-30B-A3B-Instruct",
    train_jsonl="/data1/liwenxi/agent/GenVideo/trainer_mme_data/video_mme_trainer_train.jsonl",
    eval_jsonl="/data1/liwenxi/agent/GenVideo/trainer_mme_data/video_mme_trainer_eval.jsonl",
    output_dir="/data1/liwenxi/agent/GenVideo/trainer_output/qwen3_omni_sft",
    max_train_samples=None,
    max_eval_samples=None,
    use_audio_in_video=True,
    attn_implementation="flash_attention_2",
    torch_dtype="bfloat16",
    per_device_train_batch_size=1,
    per_device_eval_batch_size=1,
    gradient_accumulation_steps=8,
    learning_rate=2e-5,
    num_train_epochs=1.0,
    max_steps=-1,
    warmup_ratio=0.03,
    weight_decay=0.0,
    logging_steps=10,
    save_steps=200,
    eval_steps=200,
    save_total_limit=2,
    seed=42,
    gradient_checkpointing=True,
    bf16=True,
    fp16=False,
    report_to="none",

    use_deepspeed=True,
    use_lora=True,
)


LORA_CONFIG: Dict[str, Any] = {
    "r": 8,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "bias": "none",
    "task_type": "CAUSAL_LM",
    "target_modules": "all-linear",
}


DEEPSPEED_CONFIG: Dict[str, Any] = {
    "train_micro_batch_size_per_gpu": CONFIG.per_device_train_batch_size,
    "gradient_accumulation_steps": CONFIG.gradient_accumulation_steps,
    "gradient_clipping": 1.0,
    "zero_optimization": {
        "stage": 3,
        "overlap_comm": True,
        "contiguous_gradients": True,
        "reduce_scatter": True,
        "allgather_partitions": True,
        "allgather_bucket_size": 200000000,
        "reduce_bucket_size": 200000000,
        "offload_optimizer": {
            "device": "cpu",
            "pin_memory": True,
        },
        "offload_param": {
            "device": "cpu",
            "pin_memory": True,
        },
    },
    "bf16": {
        "enabled": CONFIG.bf16,
    },
    "fp16": {
        "enabled": CONFIG.fp16,
        "loss_scale": 0,
        "initial_scale_power": 16,
        "hysteresis": 2,
        "min_loss_scale": 1,
    },
    "activation_checkpointing": {
        "partition_activations": True,
        "contiguous_memory_optimization": True,
        "cpu_checkpointing": False,
    },
    "wall_clock_breakdown": False,
}


def main():
    args = CONFIG
    set_seed(args.seed)

    os.makedirs(args.output_dir, exist_ok=True)

    print("Loading processor and model...")
    model_dtype = parse_dtype(args.torch_dtype)
    if model_dtype == "auto" and args.attn_implementation == "flash_attention_2":
        model_dtype = torch.bfloat16
        print("[WARN] flash_attention_2 requires explicit dtype, fallback to bfloat16.")

    model_load_kwargs = {
        "attn_implementation": args.attn_implementation,
    }
    if model_dtype != "auto":
        model_load_kwargs["dtype"] = model_dtype

    try:
        model = Qwen3OmniMoeForConditionalGeneration.from_pretrained(
            args.model_path,
            **model_load_kwargs,
        )
    except TypeError:
        if "dtype" in model_load_kwargs:
            model_load_kwargs["torch_dtype"] = model_load_kwargs.pop("dtype")
        model = Qwen3OmniMoeForConditionalGeneration.from_pretrained(
            args.model_path,
            **model_load_kwargs,
        )

    if args.use_lora:
        peft_config = LoraConfig(
            r=LORA_CONFIG["r"],
            lora_alpha=LORA_CONFIG["lora_alpha"],
            lora_dropout=LORA_CONFIG["lora_dropout"],
            bias=LORA_CONFIG["bias"],
            task_type=LORA_CONFIG["task_type"],
            target_modules=LORA_CONFIG["target_modules"],
        )
        if hasattr(model, "enable_input_require_grads"):
            try:
                model.enable_input_require_grads()
            except NotImplementedError:
                print("[WARN] enable_input_require_grads is not supported by this model, skip it.")
        model = get_peft_model(model, peft_config)
        model.print_trainable_parameters()

    processor = Qwen3OmniMoeProcessor.from_pretrained(args.model_path)

    model.config.use_cache = False
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable()

    print("Loading dataset...")
    train_records = read_jsonl(args.train_jsonl, args.max_train_samples)
    eval_records = read_jsonl(args.eval_jsonl, args.max_eval_samples) if args.eval_jsonl else None

    if len(train_records) == 0:
        raise ValueError("train_jsonl has no samples.")

    train_dataset = OmniSFTDataset(train_records, processor, args.use_audio_in_video)
    eval_dataset = OmniSFTDataset(eval_records, processor, args.use_audio_in_video) if eval_records else None

    data_collator = OmniDataCollator(processor)

    evaluation_strategy = "steps" if eval_dataset is not None else "no"

    training_args = TrainingArguments(
        output_dir=args.output_dir,
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        num_train_epochs=args.num_train_epochs,
        max_steps=args.max_steps,
        warmup_ratio=args.warmup_ratio,
        weight_decay=args.weight_decay,
        logging_steps=args.logging_steps,
        save_steps=args.save_steps,
        eval_steps=args.eval_steps,
        eval_strategy=evaluation_strategy,
        save_total_limit=args.save_total_limit,
        bf16=args.bf16,
        fp16=args.fp16,
        gradient_checkpointing=args.gradient_checkpointing,
        dataloader_num_workers=0,
        remove_unused_columns=False,
        report_to=args.report_to,
        seed=args.seed,
        deepspeed=DEEPSPEED_CONFIG if args.use_deepspeed else None,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=data_collator,
        tokenizer=processor.tokenizer,
    )

    print("Start training...")
    trainer.train()

    print("Saving model...")
    trainer.save_model(args.output_dir)
    processor.save_pretrained(args.output_dir)
    print(f"Done. Saved to: {args.output_dir}")


if __name__ == "__main__":
    main()
