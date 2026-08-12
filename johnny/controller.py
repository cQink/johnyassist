import logging
import threading
import time

from . import actions, activity, audio, history, sounds
from .app import Outcome, handle_command
from .listener import recover_latin_prefix, recover_misheard_name, strip_wake_word
from .router import contains_stop_word

logger = logging.getLogger(__name__)

# Пауза перед повторной попыткой после сбоя цикла. Держится ради сбоев,
# которые возвращаются мгновенно: MicrophoneError сам по себе приходит не
# раньше таймаута чтения, а вот падение TTS или обработчика — сразу, и без
# задержки цикл крутится на полной скорости одного ядра CPU, заливая
# нератируемый johnny.log трейсбеками. 1 секунда — достаточно, чтобы не
# грузить CPU и диск, и достаточно коротко, чтобы не выглядеть «зависшим»
# для реальных временных сбоев (например, устройство переподключилось).
_RETRY_PAUSE_SECONDS = 1.0

# Сколько сбоев ПОДРЯД терпим, прежде чем остановить прослушивание
# самостоятельно. Счётчик сбрасывается любым успешным циклом — устойчивая
# (не подряд) череда редких сбоев цикл не остановит. 5 подряд — это уже не
# «микрофон на секунду моргнул», а стабильно нерабочее состояние (например,
# устройство отключено физически); дальше спинить смысла нет, лучше
# остановиться явно и оставить в логе одну ясную запись вместо бесконечного
# потока трейсбеков.
_MAX_CONSECUTIVE_FAILURES = 5

# Сколько тишины после имени считаем паузой. Пользователь выбрал 0.7с;
# 0.75 — то же самое, выровненное по сетке блоков 0.25с (ровно 3 блока).
# Округляем ВВЕРХ намеренно: 0.7 выбрано, чтобы Джони не перебивал звуком
# естественный зазор внутри фразы, и округление вниз сломало бы смысл.
_CONTINUE_WINDOW = 0.75

# Сколько секунд после того, как в потоке Vosk промелькнуло имя «Джони»,
# последующее «стоп» ещё засчитывается как обращение к нему (см.
# _check_for_stop_while_busy). ИСТОРИЯ РЕШЕНИЯ (2026-08-03): раньше «стоп»
# определялся записью через record_until_silence + Whisper — и ломался на
# ДВУХ разных живых тестах по ДВУМ противоположным причинам: (1) полная
# модель Vosk оказалась медленнее — пока она подтверждала имя, реальное время
# уходило вперёд, и preroll (1.5с) не успевал захватить само «Джони» вместе
# со «стоп»; (2) короткое окно тишины (введено для скорости) обрывало запись
# ровно в естественной паузе между именем и «стоп», раньше чем «стоп»
# успевали произнести. Обе причины — следствие того, что весь механизм
# завязан на ТАЙМИНГИ записи одного фиксированного куска звука. Новый
# механизм таймингов не завязан вовсе: реагирует прямо на растущий частичный
# результат Vosk (без отдельной записи и без Whisper), а память в 3 секунды
# нужна только на случай, если Vosk сам разобьёт «Джони» и «стоп» на два
# разных внутренних высказывания из-за паузы между ними.
_NAME_MEMORY_SECONDS = 3.0


class AssistantController:
    """Управляемый цикл прослушивания: пауза / стоп / последняя команда.

    Выполнение команды (речь, действия) идёт в ФОНОВОМ потоке (см. busy),
    поэтому цикл прослушивания продолжает слушать микрофон, пока Джони
    говорит или что-то делает — это даёт голосовой команде «Джони, стоп»
    возможность перебить его на лету, а не только между ходами.
    """

    def __init__(self, config, speaker):
        self.config = config
        self.speaker = speaker
        self.last_command = ""
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        # Когда (time.monotonic()) в потоке Vosk последний раз промелькнуло
        # имя «Джони», пока Джони занят — None, если не звали. См.
        # _check_for_stop_while_busy и _NAME_MEMORY_SECONDS.
        self._name_heard_at: float | None = None

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def busy(self) -> bool:
        """Фоновый поток сейчас выполняет предыдущую команду?"""
        return self._worker is not None and self._worker.is_alive()

    def pause(self) -> None:
        self._paused.set()
        # Фазу ставим здесь, а не только в run_one_cycle: в момент паузы цикл
        # обычно ЖДЁТ имя внутри wait_for_wake_word и до проверки флага дойдёт
        # неизвестно когда. Кружок в панели гаснуть должен сразу по нажатию —
        # погашенный кружок это единственное, чем «на паузе» отличается от
        # «слушаю» для того, кто не читает текст (см. visualizer._LIT).
        activity.set_phase(activity.PAUSED)

    def resume(self) -> None:
        self._paused.clear()
        # Симметрично: снимаем «паузу» сразу, не дожидаясь цикла. Дальше фазу
        # перепишет сам цикл — IDLE тут только чтобы кружок не остался
        # погашенным на то время, пока цикл дойдёт до следующей проверки.
        activity.set_phase(activity.IDLE)

    def stop(self) -> None:
        self._stop.set()

    def run_one_cycle(self, listener, recognizer, mic) -> None:
        if self._paused.is_set() or self._stop.is_set():
            activity.set_phase(activity.PAUSED if self._paused.is_set() else activity.IDLE)
            mic.flush()
            return
        if self.busy:
            # Занят — не пишем отдельную запись и не зовём Whisper вовсе (см.
            # _check_for_stop_while_busy), поэтому обычный флаш микрофона
            # здесь неуместен: это не «конец хода», а один блок непрерывного
            # потока, и флаш стёр бы контекст, нужный для накопления
            # частичного результата Vosk между блоками.
            self._check_for_stop_while_busy(listener, mic)
            return
        activity.set_phase(activity.IDLE)
        listener.wait_for_wake_word(mic)
        if self._paused.is_set() or self._stop.is_set():
            mic.flush()
            return
        # Имя подтверждено — с этого момента кружок в панели горит. Раньше
        # здесь не было ни звука, ни ack (см. ниже про приглушение), и человек
        # до самого ответа не видел ни одного признака, что его услышали.
        activity.set_phase(activity.WAKE)
        # ДИАГНОСТИКА (2026-08-04): жалоба «слишком долго до "сэр?"» — живой
        # замер пользователя дал 3.41с от конца слова «Джони» до ответа.
        # Тайминг нужен, чтобы увидеть, где именно уходит время: подтверждение
        # имени Vosk'ом само по себе не измеряется (см. wait_for_wake_word),
        # но ожидание окна тишины и транскрипция preroll Whisper'ом — да.
        wake_at = time.monotonic()
        # Чтобы не играть звук и не приглушать систему до появления реальной
        # команды, здесь больше нет ни сигнала «услышал имя», ни ack.
        preroll = mic.preroll()
        activity.set_phase(activity.LISTEN)
        tail, started = audio.record_until_silence(mic, start_timeout=_CONTINUE_WINDOW)
        logger.info(
            "Тайминг: окно тишины +%.2fс от подтверждения имени (started=%s)",
            time.monotonic() - wake_at,
            started,
        )
        try:
            self._joined_turn(listener, recognizer, mic, preroll + tail, started, wake_at)
        finally:
            # Свой же звук (ack, ответ TTS) не должен вернуться на вход:
            # поток открыт постоянно, и Джони способен разбудить сам себя.
            mic.flush()
            # Гасим только если фоновый поток уже отработал: иначе кружок
            # погас бы, пока Джони ещё говорит — команда ушла в фон и живёт
            # дольше этого цикла (см. _spawn_worker).
            if not self.busy:
                activity.set_phase(activity.IDLE)

    def _check_for_stop_while_busy(self, listener, mic) -> None:
        """Джони уже занят (говорит/делает предыдущую команду).

        НЕ пишет отдельную запись и НЕ зовёт Whisper (история решения и
        причина отказа от прежней схемы — см. _NAME_MEMORY_SECONDS): читает
        ОДИН блок звука и проверяет растущий частичный результат Vosk на
        то же имя и слово «стоп» — тот же поток, что и wait_for_wake_word,
        без дополнительной записи, без ожидания тишины и без Whisper.

        Реагирует ТОЛЬКО на явное «Джони» + «стоп» вместе (см.
        _NAME_MEMORY_SECONDS про «вместе» — не обязательно в одном и том же
        частичном результате). Без подтверждения Whisper'ом — сознательный
        компромисс ради скорости и надёжности; худший случай ложного
        срабатывания — Джони замолчит, когда не просили, не более того.
        """
        text = listener.recognize(mic.read_block()).lower()
        if any(word in text for word in listener.wake_words):
            self._name_heard_at = time.monotonic()
        if self._name_heard_at is None:
            return
        if time.monotonic() - self._name_heard_at > _NAME_MEMORY_SECONDS:
            return
        if not contains_stop_word(text):
            return
        logger.info("«Стоп» посреди выполнения — прерываю")
        self._cancel.set()
        sounds.stop_all()
        history.add(text, "стоп(перебил)")
        listener.reset()
        self._name_heard_at = None

    def _spawn_worker(self, text: str, history_line) -> None:
        """Выполнить команду в фоновом потоке.

        history_line(via) -> строка для history.add — у слитного и
        обычного вызова разный формат пометки, поэтому формат передаётся
        вызывающим, а не зашивается здесь.

        Отдельный от предыдущего threading.Event: старый мог быть уже
        set() командой «стоп», и новую команду он не должен сразу обрывать.
        """
        cancel = threading.Event()
        self._cancel = cancel

        def _run() -> None:
            # Временные метки начала/конца — диагностика жалобы «стоп не
            # прерывает»: по ним видно, сколько РЕАЛЬНО длился фоновый поток,
            # и можно сверить с моментом, когда пришёл «стоп» (см. лог в
            # _check_for_stop_while_busy).
            logger.info("Фоновая команда начата: %r", text)
            # Фазу ставит и снимает сам поток: он живёт дольше цикла
            # прослушивания, и гасить кружок в run_one_cycle нельзя — Джони
            # к тому моменту ещё говорит.
            activity.set_phase(activity.SPEAK)
            try:
                # use_brain=True явно: _spawn_worker вызывается только тогда,
                # когда имя уже подтверждено (или это обычный вызов с паузой) —
                # модель-корректор спрашивать можно в обоих случаях.
                outcome = handle_command(
                    text, self.config, self.speaker, use_brain=True, cancel=cancel
                )
            except Exception:
                logger.exception("Ошибка в фоновом выполнении команды")
                history.add(text, history_line("ошибка"))
                logger.info("Фоновая команда завершена (ошибка): %r", text)
                return
            finally:
                # Через finally, а не в конце тела: у ветки с ошибкой свой
                # return, и без этого упавшая команда оставила бы кружок
                # гореть навсегда.
                activity.set_phase(activity.IDLE)
            logger.info("Фоновая команда завершена: %r", text)
            history.add(text, history_line(outcome.via))

        self._worker = threading.Thread(target=_run, daemon=True)
        self._worker.start()

    def _joined_turn(self, listener, recognizer, mic, raw, started, wake_at) -> None:
        """Решение «сказал слитно» vs «позвал и замолчал» — ДВА независимых
        сигнала, а не один: `started` (речь пошла сразу после имени — быстрый
        путь, не зависит от Whisper вообще) ИЛИ Whisper реально расслышал имя
        И команду после него в preroll (медленный путь — ловит случай, когда
        имя подтвердилось не сразу, а фраза уже вся отзвучала). В «позвал и
        ждёт» уходим, только если ОБА сигнала говорят «пусто».

        ЖИВОЙ БАГ #1 (2026-08-03, починен): раньше решение строилось ТОЛЬКО
        на `started`. После перехода на полную модель Vosk (см. память)
        подтверждение имени стало занимать заметно больше времени — короткая
        слитная команда («полный экран») успевала прозвучать и закончиться ДО
        того, как `record_until_silence` начинал слушать «новую» речь.
        Таймер тишины поэтому видел только тишину и ВСЕГДА уходил в «позвал и
        ждёт» — Джони переспрашивал «сэр?» на каждую слитную команду, хотя
        вся фраза целиком уже лежала в preroll.
        ЖИВОЙ БАГ #2 (2026-08-03, починен): решение ТОЛЬКО по транскрипции
        (без учёта `started`) сломало обратный случай — если человек ДЕЙСТВИ-
        ТЕЛЬНО позвал и замолчал, Whisper иногда не может надёжно
        расслышать одинокое короткое «Джони» без ничего после (короткие
        изолированные фразы вообще даются Whisper хуже длинных) — found
        оказывался False, и Джони просто молчал, вместо «сэр?». `started`
        как независимый быстрый сигнал это чинит: если новой речи правда не
        было, ack играет ВСЕГДА, независимо от того, расслышал ли Whisper имя.
        """
        activity.set_phase(activity.THINK)
        text, found = strip_wake_word(
            recognizer.transcribe(audio.to_float32(raw)), listener.wake_words
        )
        logger.info(
            "Тайминг: транскрипция preroll +%.2fс от подтверждения имени",
            time.monotonic() - wake_at,
        )
        if not started and not (found and text.strip()):
            # Ни один из двух сигналов не подтвердил речь после имени —
            # позвал и правда замолчал. Ждём команду отдельно, со
            # звуком-подтверждением.
            self._summoned_turn(recognizer, mic, wake_at)
            return
        mark = "слитно" if found else "слитно(без имени)"
        if not found:
            # Whisper не теряет имя, а подменяет его чужим словом («не»,
            # «желание»). Если без первого слова получается команда, а с ним
            # не получается ничего — звали именно Джони.
            recovered = recover_misheard_name(text, self.config.commands)
            if recovered is not None:
                logger.info("Имя услышано как %r — восстановлено", text.split()[0])
                text, found = recovered, True
                mark = "слитно(имя восстановлено)"
            else:
                # Второй, более широкий рубеж: короткий латинский мусор
                # первым словом вместо активатора (живой баг «vd», не
                # обязательно команда — recover_misheard_name это не ловит).
                latin_recovered = recover_latin_prefix(text)
                if latin_recovered is not None:
                    logger.info(
                        "Имя услышано как %r (латиница) — восстановлено", text.split()[0]
                    )
                    text, found = latin_recovered, True
                    mark = "слитно(имя восстановлено: латиница)"
        mark = f"{mark}[{recognizer.last_confidence:.2f}]"
        self.last_command = text
        logger.info("Распознано слитно: %r (имя %s)", text, "есть" if found else "НЕТ")

        if not found:
            # Похоже на ложное срабатывание Vosk: молчим и не зовём мозг.
            # У «не понял»/«не расслышал» — короткая кешированная озвучка,
            # ждать фоновый поток тут незачем, решаем сразу.
            outcome = handle_command(text, self.config, self.speaker, speak_failures=False, use_brain=False)
            history.add(text, f"{mark}/{outcome.via}")
            return

        # Имя подтверждено — дальше Джони может говорить/делать долго (TTS,
        # цепочка, браузер). Запускаем в фоне, чтобы «Джони, стоп» можно было
        # услышать, пока он ещё занят (см. busy/_check_for_stop_while_busy).
        #
        # ПЛАТА: раньше непонятая слитная команда с именем сама переходила в
        # режим «позвал и ждёт» (ack + повторный вопрос, см. _summoned_turn).
        # В фоне так сделать нельзя — второй поток не может одновременно с
        # главным читать общий микрофон. Теперь непонятая команда просто
        # отвечает «Не понял команду» (см. handle_command, speak_failures по
        # умолчанию True) без автоматического повторного приглашения — надо
        # позвать заново.
        self._spawn_worker(text, lambda via: f"{mark}/{via}")

    def _summoned_turn(self, recognizer, mic, wake_at) -> None:
        """Позвал и ждёт: звук-подтверждение, потом команда."""
        logger.info(
            "Тайминг: жду команду после имени +%.2fс", time.monotonic() - wake_at
        )
        mic.flush()  # иначе в запись попадёт собственный «пик»
        raw, started = audio.record_until_silence(mic)
        text = recognizer.transcribe(audio.to_float32(raw)) if started else ""
        self.last_command = text
        logger.info("Распознано: %r", text)
        conf = recognizer.last_confidence
        self._spawn_worker(text, lambda via: f"{via}[{conf:.2f}]")

    def run(self, listener, recognizer, mic) -> None:
        consecutive_failures = 0
        while not self._stop.is_set():
            try:
                self.run_one_cycle(listener, recognizer, mic)
                consecutive_failures = 0
            except Exception:
                # Сбой (например, TTS, либо пропавший микрофон) внутри одного
                # цикла не должен убивать поток прослушивания — трей-иконка
                # иначе выглядит живой, а Джони уже не слушает.
                logger.exception("Ошибка в цикле прослушивания")
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "Слишком много сбоев подряд (%d) — прослушивание остановлено",
                        consecutive_failures,
                    )
                    self.stop()
                    break
                time.sleep(_RETRY_PAUSE_SECONDS)
