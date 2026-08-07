#!/usr/bin/env python
"""SenseVoice-Small INT8 ONNX 推理脚本"""

import argparse
import json
import os
from pathlib import Path

import librosa
import numpy as np
import onnxruntime
import soundfile as sf


def load_cmvn(cmvn_path):
    with open(cmvn_path, encoding="utf-8") as f:
        lines = f.readlines()
    bias_str = lines[4].split("[")[1].split("]")[0]
    scale_str = lines[6].split("[")[1].split("]")[0]
    bias = np.array([float(x) for x in bias_str.strip().split()], dtype=np.float32)
    scale = np.array([float(x) for x in scale_str.strip().split()], dtype=np.float32)
    means = -bias / (scale + 1e-10)
    vars_ = 1.0 / (scale**2 + 1e-10)
    return means, vars_


def compute_fbank(wav, sr=16000, n_mels=80, frame_length=25, frame_shift=10):
    if sr != 16000:
        wav = librosa.resample(wav, orig_sr=sr, target_sr=16000)
        sr = 16000
    win_len = int(frame_length * sr / 1000)
    hop_len = int(frame_shift * sr / 1000)
    S = librosa.feature.melspectrogram(
        y=wav,
        sr=sr,
        n_mels=n_mels,
        n_fft=512,
        hop_length=hop_len,
        win_length=win_len,
        window="hamming",
        center=True,
        pad_mode="reflect",
        power=2.0,
    )
    log_S = librosa.power_to_db(S, ref=np.max, top_db=None)
    return log_S.T.astype(np.float32)


def apply_lfr(feat, m=7, n=6):
    T, D = feat.shape
    padding = np.zeros([(m - 1) // 2, D], dtype=np.float32)
    feat_pad = np.concatenate([padding, feat, padding], axis=0)
    out_frames = []
    for i in range(0, T, n):
        end = i + m
        if end > feat_pad.shape[0]:
            break
        out_frames.append(feat_pad[i:end].reshape(-1))
    if not out_frames:
        return np.empty((0, D * m), dtype=np.float32)
    return np.stack(out_frames, axis=0)


def apply_cmvn(feat, means, vars_):
    return (feat - means) * (vars_**-0.5)


# 语言映射: SenseVoice 支持的语言ID
LANG_MAP = {
    "auto": 0,
    "zh": 3,
    "en": 4,
    "yue": 7,
    "ja": 11,
    "ko": 12,
    "es": 13,
    "fr": 14,
    "de": 15,
    "pt": 16,
}


class SenseVoiceONNX:
    def __init__(self, model_dir):
        model_dir = Path(model_dir)
        self.model_path = str(model_dir / "model_quant.onnx")
        self.cmvn_path = str(model_dir / "am.mvn")
        self.tokens_path = str(model_dir / "tokens.json")
        self.config_path = str(model_dir / "config.yaml")

        with open(self.tokens_path, encoding="utf-8") as f:
            self.tokens = json.load(f)

        self.means, self.vars = load_cmvn(self.cmvn_path)

        so = onnxruntime.SessionOptions()
        so.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = 4
        self.session = onnxruntime.InferenceSession(
            self.model_path, sess_options=so, providers=["CPUExecutionProvider"]
        )

        # 解析模型输入输出
        self.inputs = {i.name: i for i in self.session.get_inputs()}
        self.outputs = [o.name for o in self.session.get_outputs()]

        import yaml

        with open(self.config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        fc = cfg.get("frontend_conf", {})
        self.n_mels = fc.get("n_mels", 80)
        self.frame_length = fc.get("frame_length", 25)
        self.frame_shift = fc.get("frame_shift", 10)
        self.lfr_m = fc.get("lfr_m", 7)
        self.lfr_n = fc.get("lfr_n", 6)

    def preprocess(self, audio_path):
        wav, sr = sf.read(audio_path)
        feat = compute_fbank(wav, sr, self.n_mels, self.frame_length, self.frame_shift)
        feat = apply_lfr(feat, self.lfr_m, self.lfr_n)
        if feat.shape[0] == 0:
            raise ValueError("音频太短")
        feat = apply_cmvn(feat, self.means, self.vars)
        feat = np.expand_dims(feat, axis=0).astype(np.float32)
        return feat

    def infer(self, audio_path, language="auto"):
        feat = self.preprocess(audio_path)
        feat_len = np.array([feat.shape[1]], dtype=np.int32)
        lang = np.array([LANG_MAP.get(language, 0)], dtype=np.int32)
        textnorm = np.array([1], dtype=np.int32)  # 1=带标点归一化

        feed = {
            "speech": feat,
            "speech_lengths": feat_len,
            "language": lang,
            "textnorm": textnorm,
        }
        return self.session.run(self.outputs, feed)

    def decode_ctc(self, outputs) -> tuple[str, str]:
        """Decode CTC logits to (text, detected_language).

        SenseVoice prefixes rich tags such as ``<|zh|><|NEUTRAL|><|Speech|><|woitn|>``.
        Strip whole ``<|...|>`` tags — do NOT strip only ``<|>`` brackets (that leaves
        ``zhNEUTRALSpeechwoitn`` glued onto the transcript).
        """
        import re

        text, detected_lang = "", "unknown"
        tag_re = re.compile(r"<\|[^|>]*\|>")
        lang_tags = {
            "<|zh|>": "zh",
            "<|en|>": "en",
            "<|yue|>": "yue",
            "<|ja|>": "ja",
            "<|ko|>": "ko",
            "<|zh/en|>": "zh",
            "<|en/zh|>": "en",
        }
        for name, out in zip(self.outputs, outputs, strict=False):
            if "logits" not in name or out.ndim != 3:
                continue
            tok_ids = out.argmax(axis=-1)[0]
            chars = []
            prev = -1
            for t in tok_ids:
                if t != prev and t > 4 and t < len(self.tokens):
                    chars.append(self.tokens[t])
                prev = t
            raw = "".join(chars)
            for tag, lang in lang_tags.items():
                if tag in raw:
                    detected_lang = lang
                    break
            text = tag_re.sub("", raw)
            text = text.replace("<s>", "").replace("</s>", "").replace("<unk>", "").replace("▁", " ")
            text = re.sub(r"\s+", " ", text).strip()
            break
        return text or "", detected_lang


def main():
    parser = argparse.ArgumentParser(description="SenseVoice-Small ONNX 推理")
    parser.add_argument("--model_dir", default="model")
    parser.add_argument("--language", default="auto", choices=list(LANG_MAP.keys()))
    parser.add_argument("audio", nargs="?", help="音频文件路径")
    args = parser.parse_args()

    MODEL_DIR = os.path.join(os.path.dirname(__file__), args.model_dir)
    engine = SenseVoiceONNX(MODEL_DIR)
    if args.audio:
        engine.infer(args.audio, language=args.language)
    else:
        print("模型加载成功。使用: python inference.py --language zh <audio.wav>")


if __name__ == "__main__":
    main()
