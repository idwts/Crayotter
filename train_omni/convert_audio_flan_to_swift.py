#!/usr/bin/env python3
"""
Convert Video-MME dataset to ms-swift standard JSONL format.

ms-swift multimodal dataset format with video:
{
    "messages": [
        {"role": "user", "content": "<video>question text"},
        {"role": "assistant", "content": "answer"}
    ],
    "videos": ["/path/to/video.mp4"]
}

The <video> tag marks where video features should be inserted.
"""

import os
import json
import argparse
from datasets import load_dataset


def convert_video_mme(
    output_dir: str = "./data/video_mme_swift",
    video_dir: str = "./data/video_mme_videos",  # Where the video files are stored locally
    max_samples: int = None,
):
    """Convert Video-MME dataset to ms-swift format."""
    
    os.makedirs(output_dir, exist_ok=True)
    
    print("Loading Video-MME dataset...")
    dataset = load_dataset("lmms-lab/Video-MME", split="test")
    
    if max_samples:
        dataset = dataset.select(range(min(max_samples, len(dataset))))
    
    train_output = os.path.join(output_dir, "video_mme_train.jsonl")
    eval_output = os.path.join(output_dir, "video_mme_eval.jsonl")
    
    # Split 90/10 for train/eval
    split_idx = int(len(dataset) * 0.9)
    
    for split_name, start_idx, end_idx, output_path in [
        ("train", 0, split_idx, train_output),
        ("eval", split_idx, len(dataset), eval_output),
    ]:
        print(f"Processing {split_name} split ({end_idx - start_idx} samples)...")
        
        with open(output_path, "w", encoding="utf-8") as f:
            for i in range(start_idx, end_idx):
                example = dataset[i]
                
                # Build question with options
                question = example.get("question", "")
                options = ""
                for opt_key in ["optionA", "optionB", "optionC", "optionD"]:
                    if opt_key in example and example[opt_key]:
                        letter = opt_key.replace("option", "")
                        options += f"{letter}. {example[opt_key]}\n"
                
                # Include subtitles if available
                subtitle_text = ""
                if example.get("subtitle", ""):
                    subtitle_text = f"This video's subtitles are listed below:\n{example['subtitle']}\n"
                
                full_question = (
                    f"{subtitle_text}"
                    f"Select the best answer to the following multiple-choice question "
                    f"based on the video. Respond with only the letter (A, B, C, or D) "
                    f"of the correct option.\n"
                    f"{question}\n{options}"
                    f"The best answer is:"
                )
                
                answer = example.get("answer", "A")
                
                # Video path — adjust based on your local video storage
                video_id = example.get("videoID", example.get("video_id", ""))
                video_path = os.path.join(os.path.abspath(video_dir), f"{video_id}.mp4")
                
                # If you have the video URLs directly, you can also use URLs
                # video_path = example.get("url", video_path)
                
                record = {
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are Qwen-Omni, a helpful AI assistant with video understanding capabilities."
                        },
                        {
                            "role": "user",
                            "content": f"<video>{full_question}"
                        },
                        {
                            "role": "assistant",
                            "content": answer
                        }
                    ],
                    "videos": [video_path]
                }
                
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        
        print(f"  Saved to {output_path}")
    
    print("Done! Video-MME conversion complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output_dir", default="./data/video_mme_swift")
    parser.add_argument("--video_dir", default="./data/video_mme_videos")
    parser.add_argument("--max_samples", type=int, default=None)
    args = parser.parse_args()
    
    convert_video_mme(
        output_dir=args.output_dir,
        video_dir=args.video_dir,
        max_samples=args.max_samples,
    )