"""Диагностика активатора: печатает всё, что слышит Vosk. Запуск: python wake_check.py"""
import json
import queue
import time

import sounddevice as sd
import winsound
from vosk import KaldiRecognizer, Model

from johnny.config import load_config

s = load_config("config").settings
print("Загружаю Vosk...")
model = Model(s.vosk_model_path)
rec = KaldiRecognizer(model, 16000)

q: queue.Queue = queue.Queue()


def _cb(indata, frames, t, status):
    q.put(bytes(indata))


DURATION = 15
print(f"После сигнала {DURATION} сек говори «джони» несколько раз, чётко.")
time.sleep(1.0)
winsound.Beep(1000, 300)

results = []
end = time.time() + DURATION
with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16", channels=1, callback=_cb):
    while time.time() < end:
        try:
            data = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if rec.AcceptWaveform(data):
            txt = json.loads(rec.Result()).get("text", "")
            if txt:
                results.append(txt)
    final = json.loads(rec.FinalResult()).get("text", "")
    if final:
        results.append(final)

winsound.Beep(500, 200)
with open("wake_result.txt", "w", encoding="utf-8") as f:
    f.write("=== Что услышал Vosk ===\n")
    for r in results:
        f.write(repr(r) + "\n")
    if not results:
        f.write("(ничего — Vosk не получил распознаваемой речи)\n")
print("Готово. Результат записан в wake_result.txt")
