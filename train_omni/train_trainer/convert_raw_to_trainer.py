#!/usr/bin/env python3
"""
Convert raw datasets to trainer JSONL format used by train_qwen3_omni_trainer.py.

Output format (one JSON per line):
{
  "conversation": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": [
      {"type": "video", "video": "/abs/path/to/xxx.mp4"},
      {"type": "text", "text": "question..."}
    ]}
  ],
  "response": "assistant target text"
}

Supports datasets:
- lmms-lab/Video-MME
- HKUSTAudio/Audio-FLAN-Dataset
"""

import os
import json
import argparse
from typing import Dict, Any, Optional, Iterable, List

from datasets import load_dataset, Audio


def build_video_mme_prompt(example: Dict[str, Any]) -> str:
    question = example.get("question", "")
    options: List[str] = []
    for opt_key in ["optionA", "optionB", "optionC", "optionD"]:
        value = example.get(opt_key)
        if value:
            letter = opt_key.replace("option", "")
            options.append(f"{letter}. {value}")

    subtitle = example.get("subtitle", "")
    subtitle_text = ""
    if subtitle:
        subtitle_text = f"This video's subtitles are listed below:\n{subtitle}\n"

    option_text = "\n".join(options)
    prompt = (
        f"{subtitle_text}"
        "Select the best answer to the following multiple-choice question based on the video. "
        "Respond with only the letter (A, B, C, or D) of the correct option.\n"
        f"{question}\n"
        f"{option_text}\n"
        "The best answer is:"
    )
    return prompt


def build_video_mme_record(example: Dict[str, Any], video_dir: str) -> Dict[str, Any]:
    video_id = example.get("videoID", example.get("video_id", ""))
    video_path = os.path.abspath(os.path.join(video_dir, f"{video_id}.mp4"))

    user_content = [
        {"type": "video", "video": video_path},
        {"type": "text", "text": build_video_mme_prompt(example)},
    ]

    record = {
        "conversation": [
            {
                "role": "system",
                "content": "You are Qwen-Omni, a helpful AI assistant with video understanding capabilities.",
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        "response": str(example.get("answer", "A")),
        "meta": {
            "source": "Video-MME",
            "video_id": video_id,
        },
    }
    return record


def build_audio_flan_record(example: Dict[str, Any], audio_path: str) -> Dict[str, Any]:
    instruction = str(example.get("instruction", "")).strip()
    response = str(example.get("response", "")).strip()

    record = {
        "conversation": [
            {
                "role": "system",
                "content": "You are Qwen-Omni, a helpful AI assistant with audio understanding capabilities.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": os.path.abspath(audio_path)},
                    {"type": "text", "text": instruction},
                ],
            },
        ],
        "response": response,
        "meta": {
            "source": "Audio-FLAN",
        },
    }
    return record


def split_write(records: Iterable[Dict[str, Any]], train_path: str, eval_path: str, train_ratio: float, total: Optional[int]) -> None:
    train_count = 0
    eval_count = 0

    with open(train_path, "w", encoding="utf-8") as train_f, open(eval_path, "w", encoding="utf-8") as eval_f:
        for i, record in enumerate(records):
            if total is None:
                to_eval = (i % 10 == 9)
            else:
                to_eval = i >= int(total * train_ratio)

            if to_eval:
                eval_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                eval_count += 1
            else:
                train_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                train_count += 1

            if (i + 1) % 500 == 0:
                print(f"  Processed {i + 1} samples (train={train_count}, eval={eval_count})")

    print("Conversion done.")
    print(f"  Train: {train_path} ({train_count})")
    print(f"  Eval:  {eval_path} ({eval_count})")


def convert_video_mme(
    output_dir: str,
    video_dir: str,
    max_samples: Optional[int],
    train_ratio: float,
    hf_token: Optional[str],
) -> None:
    print("Loading Video-MME...")
    dataset = load_dataset("lmms-lab/Video-MME", split="test", token=hf_token)

    if max_samples is not None:
        dataset = dataset.select(range(min(max_samples, len(dataset))))

    total = len(dataset)

    def gen_records():
        for example in dataset:
            yield build_video_mme_record(example, video_dir)

    train_path = os.path.join(output_dir, "video_mme_trainer_train.jsonl")
    eval_path = os.path.join(output_dir, "video_mme_trainer_eval.jsonl")
    split_write(gen_records(), train_path, eval_path, train_ratio=train_ratio, total=total)


def convert_audio_flan(
    output_dir: str,
    audio_save_dir: str,
    max_samples: Optional[int],
    train_ratio: float,
    hf_token: Optional[str],
    streaming: bool,
) -> None:
    import soundfile as sf

    os.makedirs(audio_save_dir, exist_ok=True)

    print(f"Loading Audio-FLAN... (streaming={streaming})")
    dataset = load_dataset(
        "HKUSTAudio/Audio-FLAN-Dataset",
        split="train",
        streaming=streaming,
        token=hf_token,
    )
    dataset = dataset.cast_column("audio", Audio(sampling_rate=16000))

    if streaming:
        if max_samples is not None:
            dataset = dataset.take(max_samples)
        total = max_samples

        def gen_records():
            for i, example in enumerate(dataset):
                audio_array = example["audio"]["array"]
                sr = example["audio"]["sampling_rate"]
                audio_path = os.path.join(audio_save_dir, f"audio_flan_{i:08d}.wav")
                sf.write(audio_path, audio_array, sr)
                yield build_audio_flan_record(example, audio_path)

    else:
        if max_samples is not None:
            dataset = dataset.select(range(min(max_samples, len(dataset))))
        total = len(dataset)

        def gen_records():
            for i, example in enumerate(dataset):
                audio_array = example["audio"]["array"]
                sr = example["audio"]["sampling_rate"]
                audio_path = os.path.join(audio_save_dir, f"audio_flan_{i:08d}.wav")
                sf.write(audio_path, audio_array, sr)
                yield build_audio_flan_record(example, audio_path)

    train_path = os.path.join(output_dir, "audio_flan_trainer_train.jsonl")
    eval_path = os.path.join(output_dir, "audio_flan_trainer_eval.jsonl")
    split_write(gen_records(), train_path, eval_path, train_ratio=train_ratio, total=total)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=["video_mme", "audio_flan"], required=True)
    parser.add_argument("--output_dir", type=str, default="./data/trainer_data")

    parser.add_argument("--video_dir", type=str, default="./data/video_mme_videos")
    parser.add_argument("--audio_save_dir", type=str, default="./data/audio_flan_wavs")

    parser.add_argument("--max_samples", type=int, default=None)
    parser.add_argument("--train_ratio", type=float, default=0.9)
    parser.add_argument("--hf_token", type=str, default=os.environ.get("HF_TOKEN"))
    parser.add_argument("--streaming", type=lambda x: str(x).lower() in {"1", "true", "yes"}, default=True)

    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.dataset == "video_mme":
        convert_video_mme(
            output_dir=args.output_dir,
            video_dir=args.video_dir,
            max_samples=args.max_samples,
            train_ratio=args.train_ratio,
            hf_token=args.hf_token,
        )
    else:
        convert_audio_flan(
            output_dir=args.output_dir,
            audio_save_dir=args.audio_save_dir,
            max_samples=args.max_samples,
            train_ratio=args.train_ratio,
            hf_token=args.hf_token,
            streaming=args.streaming,
        )


if __name__ == "__main__":
    main()
