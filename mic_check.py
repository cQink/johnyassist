"""Диагностика микрофона + Whisper. Запуск: python test_mic.py"""
import time

import numpy as np
import sounddevice as sd
import winsound

from johnny.recognizer import Recognizer

SR = 16000
BLOCK = 4000
SECONDS = 5

print("Загружаю модель Whisper (medium, GPU)...")
r = Recognizer("medium", "cuda")

print()
print("=" * 52)
print(f"  После сигнала говори {SECONDS} секунд, например: открой ютуб")
print("=" * 52)
time.sleep(1.0)

winsound.Beep(1000, 350)  # высокий сигнал = "говори"

frames = []
peak = 0.0
with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK) as stream:
    for _ in range(int(SECONDS * SR / BLOCK)):
        block, _ = stream.read(BLOCK)
        block = block.flatten()
        frames.append(block)
        peak = max(peak, float(np.max(np.abs(block))))

winsound.Beep(500, 200)  # низкий сигнал = "записал"
audio = np.concatenate(frames)

print()
print(f"Пиковая громкость за {SECONDS} сек: {peak:.3f}")
if peak < 0.02:
    print(">>> Микрофон почти ничего не слышит!")
    print("    Проверь: кнопка MUTE на Yeti (не горит красным?),")
    print("    крутилка Gain, и что в Windows выбран Yeti как устройство ВВОДА.")
else:
    print("    (микрофон слышит звук — хорошо)")

segments, _ = r._model.transcribe(audio, language="ru")
text = " ".join(s.text.strip() for s in segments).strip()
print(f"РАСПОЗНАНО: {text!r}")
