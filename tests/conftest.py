import pytest

import johnny.http_client as http_client
import johnny.memory as memory


@pytest.fixture(autouse=True)
def _isolate_memory_files(tmp_path, monkeypatch):
    """Память Джони пишет в файлы рядом с пакетом (memory.yaml, dialog.yaml).
    record_turn сохраняет разговор САМ, на каждом обмене, — без этой изоляции
    любой тест, дошедший до модели, затирал бы живой разговор пользователя
    своим «привет/ответ», а тесты фактов зависели бы от порядка запуска.

    Отдельные тесты по-прежнему подменяют _MEMORY_FILE своим tmp_path — это
    их право, фикстура только гарантирует, что НАСТОЯЩИЕ файлы не тронуты.
    """
    monkeypatch.setattr(memory, "_MEMORY_FILE", tmp_path / "memory.yaml")
    monkeypatch.setattr(memory, "_DIALOG_FILE", tmp_path / "dialog.yaml")
    memory._turns.clear()
    yield
    memory._turns.clear()


@pytest.fixture(autouse=True)
def _reset_provider_cooldowns():
    """Пауза после отказа провайдера (fish/groq) — общее состояние модуля
    http_client. Без сброса между тестами один тест мог бы «отравить» cooldown
    для другого и заставить его молча проскочить сеть, которую как раз проверяют.
    """
    http_client._last_failure.clear()
    yield
    http_client._last_failure.clear()
