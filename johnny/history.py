from datetime import datetime
from pathlib import Path

_HISTORY_FILE = Path(__file__).resolve().parent.parent / "history.log"


def add(text: str, via: str = "") -> None:
    """Дописать одну строку в чистую историю распознаваний (со временем).

    via — как сработало: «точно» / «похоже (0.74)» / «groq» / «claude» / «не понял».
    Нужен, чтобы потом подкручивать пороги по фактам, а не на глаз.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    shown = text if text.strip() else "(пусто)"
    line = f"{stamp}  {shown}" + (f"   [{via}]\n" if via else "\n")
    with open(_HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(line)
