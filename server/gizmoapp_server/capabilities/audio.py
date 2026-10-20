from __future__ import annotations

import math
from typing import Any


MAX_AUDIO_SAMPLES = 100_000
MAX_SAMPLE_RATE = 384_000


def analyze_samples(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw_samples = payload.get("samples", [])
    sample_rate = payload.get("sampleRate", 44100)
    errors: list[str] = []

    if not isinstance(raw_samples, list) or not raw_samples:
        return {}, ["samples must be a non-empty list of numbers"]
    if len(raw_samples) > MAX_AUDIO_SAMPLES:
        return {}, [f"samples must contain at most {MAX_AUDIO_SAMPLES} values"]

    try:
        samples = [float(value) for value in raw_samples]
        sample_rate_float = float(sample_rate)
    except (TypeError, ValueError):
        return {}, ["samples and sampleRate must be numeric"]

    if not math.isfinite(sample_rate_float) or sample_rate_float <= 0 or sample_rate_float > MAX_SAMPLE_RATE:
        errors.append(f"sampleRate must be finite and between 1 and {MAX_SAMPLE_RATE}")
    if any(not math.isfinite(value) for value in samples):
        errors.append("samples must contain only finite numbers")

    if errors:
        return {}, errors

    peak = max(abs(value) for value in samples)
    rms = math.sqrt(sum(value * value for value in samples) / len(samples))
    zero_crossings = sum(
        1
        for previous, current in zip(samples, samples[1:])
        if (previous < 0 <= current) or (previous > 0 >= current)
    )

    return {
        "sampleCount": len(samples),
        "sampleRate": sample_rate_float,
        "durationSeconds": len(samples) / sample_rate_float,
        "peakAmplitude": peak,
        "rmsAmplitude": rms,
        "zeroCrossings": zero_crossings,
    }, []
