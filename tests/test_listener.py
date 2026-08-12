import pathlib

import johnny.listener as listener_module


def test_listener_falls_back_to_small_model_when_full_model_fails(monkeypatch, tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True)
    fallback_path = models_dir / "vosk-model-small-ru-0.22"
    fallback_path.mkdir()

    class FakeModel:
        def __init__(self, path):
            path_str = str(path)
            if "vosk-model-ru-0.42" in path_str:
                raise RuntimeError("Failed to create a model")
            if "vosk-model-small-ru-0.22" in path_str:
                return
            raise AssertionError("Unexpected model path: %r" % path)

    class FakeKaldiRecognizer:
        def __init__(self, model, sample_rate):
            self.model = model
            self.sample_rate = sample_rate

    monkeypatch.setattr(listener_module, "Model", FakeModel)
    monkeypatch.setattr(listener_module, "KaldiRecognizer", FakeKaldiRecognizer)
    monkeypatch.setattr(
        listener_module,
        "_resolve_model_path",
        lambda path: models_dir / pathlib.Path(path).name,
    )

    listener = listener_module.Listener("джони", "models/vosk-model-ru-0.42")

    assert listener.available is True
    assert isinstance(listener._recognizer, FakeKaldiRecognizer)
