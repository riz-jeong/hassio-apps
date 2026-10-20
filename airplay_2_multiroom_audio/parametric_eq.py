"""Generate Shairport Sync convolution filters from add-on PEQ options."""

import json
import math
import os
import struct
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path


SUPPORTED_RATES = (44100, 48000, 88200, 96000, 176400, 192000)
AUTO_RATES = (44100, 48000)
DEFAULT_FREQUENCIES = (80, 250, 1000, 4000, 12000)
IR_FRAMES = 32768


@dataclass(frozen=True)
class Band:
    frequency: float
    gain_db: float
    q: float


@dataclass(frozen=True)
class Settings:
    enabled: bool
    bands: tuple

    @property
    def active(self):
        return self.enabled and any(band.gain_db != 0 for band in self.bands)

    @property
    def max_boost_db(self):
        if not self.active:
            return 0.0
        return max(_response_peak_db(self.bands, rate) for rate in SUPPORTED_RATES)


def load_settings(options_file):
    try:
        options = json.loads(Path(options_file).read_text())
    except FileNotFoundError:
        options = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read add-on PEQ options: {exc}") from exc
    if not isinstance(options, dict):
        raise ValueError("add-on options must be a JSON object")

    enabled = options.get("airplay_eq_enabled", False)
    if type(enabled) is not bool:
        raise ValueError("airplay_eq_enabled must be true or false")

    bands = []
    for index, default_frequency in enumerate(DEFAULT_FREQUENCIES, start=1):
        frequency = _number(options, f"airplay_eq_band_{index}_frequency",
                            default_frequency, 20, 20000)
        gain = _number(options, f"airplay_eq_band_{index}_gain_db", 0, -12, 12)
        q = _number(options, f"airplay_eq_band_{index}_q", 1.0, 0.1, 5.0)
        bands.append(Band(frequency, gain, q))
    return Settings(enabled, tuple(bands))


def _number(options, key, default, minimum, maximum):
    value = options.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number from {minimum} to {maximum}")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number from {minimum} to {maximum}") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be a number from {minimum} to {maximum}")
    return value


def _coefficients(band, sample_rate):
    amplitude = 10 ** (band.gain_db / 40)
    omega = 2 * math.pi * band.frequency / sample_rate
    alpha = math.sin(omega) / (2 * band.q)
    cosine = math.cos(omega)
    a0 = 1 + alpha / amplitude
    return (
        (1 + alpha * amplitude) / a0,
        (-2 * cosine) / a0,
        (1 - alpha * amplitude) / a0,
        (-2 * cosine) / a0,
        (1 - alpha / amplitude) / a0,
    )


def _response_peak_db(bands, sample_rate):
    coefficients = [_coefficients(band, sample_rate) for band in bands]
    peak = 0.0
    for step in range(4097):
        omega = math.pi * step / 4096
        cos1, sin1 = math.cos(omega), math.sin(omega)
        cos2, sin2 = math.cos(2 * omega), math.sin(2 * omega)
        magnitude = 1.0
        for b0, b1, b2, a1, a2 in coefficients:
            numerator = complex(b0 + b1 * cos1 + b2 * cos2,
                                -b1 * sin1 - b2 * sin2)
            denominator = complex(1 + a1 * cos1 + a2 * cos2,
                                 -a1 * sin1 - a2 * sin2)
            magnitude *= abs(numerator / denominator)
        peak = max(peak, magnitude)
    return 20 * math.log10(max(peak, 1e-12))


def _cascade(bands, sample_rate):
    signal = [1.0] + [0.0] * (IR_FRAMES - 1)
    for band in bands:
        b0, b1, b2, a1, a2 = _coefficients(band, sample_rate)
        output = [0.0] * IR_FRAMES
        x1 = x2 = y1 = y2 = 0.0
        for index, current in enumerate(signal):
            value = b0 * current + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            output[index] = value
            x2, x1 = x1, current
            y2, y1 = y1, value
        signal = output
    return signal


def _write_pcm16_wav(path, samples, sample_rate, scale):
    fd, temp_path = tempfile.mkstemp(prefix="peq-", suffix=".wav", dir=path.parent)
    os.close(fd)
    try:
        with wave.open(temp_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            for offset in range(0, len(samples), 4096):
                block = samples[offset:offset + 4096]
                encoded = (max(-32768, min(32767, round(value * scale * 32767)))
                           for value in block)
                wav.writeframes(b"".join(struct.pack("<h", value) for value in encoded))
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return scale


def generate_impulse_response(settings, sample_rate, directory):
    if sample_rate not in SUPPORTED_RATES:
        supported = ", ".join(str(rate) for rate in SUPPORTED_RATES)
        raise ValueError(f"Parametric EQ supports output rates {supported} Hz; "
                         f"the selected rate is {sample_rate} Hz")
    if not settings.active:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"airplay-peq-{sample_rate}.wav"
    samples = _cascade(settings.bands, sample_rate)
    peak = max(abs(value) for value in samples)
    scale = min(1.0, 0.98 / peak) if peak else 1.0
    _write_pcm16_wav(path, samples, sample_rate, scale)
    return path


def shairport_dsp_config(options_file, sample_rate, directory):
    settings = load_settings(options_file)
    if not settings.active:
        return ""
    if sample_rate not in ("auto", *SUPPORTED_RATES):
        supported = ", ".join(str(rate) for rate in SUPPORTED_RATES)
        raise ValueError(f"Parametric EQ supports output rates {supported} Hz; "
                         f"the selected rate is {sample_rate} Hz")
    rates = AUTO_RATES if sample_rate == "auto" else (sample_rate,)
    signals = [(rate, _cascade(settings.bands, rate)) for rate in rates]
    peak = max(max(abs(value) for value in signal) for _, signal in signals)
    scale = min(1.0, 0.98 / peak) if peak else 1.0
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for rate, signal in signals:
        path = directory / f"airplay-peq-{rate}.wav"
        _write_pcm16_wav(path, signal, rate, scale)
        paths.append(path)
    encoded_paths = ", ".join(f"'{path}'" for path in paths)
    peak_db = max(_response_peak_db(settings.bands, rate) for rate in rates)
    normalization_gain = -20 * math.log10(scale) if scale < 1.0 else 0.0
    convolution_gain = max(-60.0, min(18.0,
                                     normalization_gain - max(0.0, peak_db) - 1.0))
    return f"""dsp :
{{
  convolution_enabled = \"yes\";
  convolution_thread_pool_size = 1;
  convolution_ir_files = {json.dumps(encoded_paths)};
  convolution_gain = {convolution_gain:.2f};
  convolution_max_length_in_seconds = 0.75;
}};
"""
