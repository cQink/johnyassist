from johnny import contacts

PEOPLE = {
    "гоша": {
        "discord": "Гречка",
        "username": "ne_godjaj",
        "aliases": ["гоша", "гречка", "георгий", "георгию", "гандон"],
    },
    "ярик": {
        "discord": "Грущенко",
        "username": "r1ealy",
        "aliases": ["ярик", "грущенко", "грущик"],
    },
    "дядя вася": {
        "discord": "VasyaTheGreat",
        "username": "vasya",
        "aliases": ["дядя вася"],
    },
}


def test_resolve_по_алиасу():
    assert contacts.resolve("гоша", PEOPLE)["discord"] == "Гречка"
    assert contacts.resolve("ярик", PEOPLE)["discord"] == "Грущенко"


def test_resolve_терпит_падежи():
    # «гоше» и «гоша» сходятся после отрезания хвостовой гласной
    assert contacts.resolve("гоше", PEOPLE)["discord"] == "Гречка"
    assert contacts.resolve("грущику", PEOPLE)["discord"] == "Грущенко"


def test_resolve_незнакомого_возвращает_none():
    assert contacts.resolve("петя", PEOPLE) is None


def test_split_message_отделяет_имя_от_текста():
    person, text = contacts.split_message("гоше привет как дела", PEOPLE)
    assert person["discord"] == "Гречка"
    assert text == "привет как дела"


def test_split_message_берёт_длиннейшее_имя():
    # «дядя» само по себе не алиас — имя должно съесть два слова, а не одно
    person, text = contacts.split_message("дядя вася ты где", PEOPLE)
    assert person["discord"] == "VasyaTheGreat"
    assert text == "ты где"


def test_split_message_без_текста_возвращает_none():
    assert contacts.split_message("гоше", PEOPLE) is None


def test_split_message_с_чужим_именем_возвращает_none():
    assert contacts.split_message("пете привет", PEOPLE) is None


def test_matches_dm_узнаёт_ссылку_со_статусом():
    person = PEOPLE["гоша"]
    assert contacts.matches_dm("не прочитано, Гречка (личное сообщение)", person)
    assert contacts.matches_dm("Гречка (личное сообщение)", person)


def test_matches_dm_отвергает_чужую_ссылку_и_не_лс():
    person = PEOPLE["гоша"]
    assert not contacts.matches_dm("Грущенко (личное сообщение)", person)
    assert not contacts.matches_dm("Гречка", person)


def test_matches_input_сверяет_адресата():
    person = PEOPLE["гоша"]
    assert contacts.matches_input("Написать @Гречка", person)
    assert not contacts.matches_input("Написать @Грущенко", person)
    assert not contacts.matches_input("Гречка", person)


def test_matches_input_отвергает_групповой_чат():
    """«Написать @Гречка, Ярик» — групповой чат, сообщение прочитают чужие.

    Сверка вхождением такое пропускала: имя нужного человека в названии поля
    есть, а то, что он там не один, никого не смущало.
    """
    person = PEOPLE["гоша"]
    assert not contacts.matches_input("Написать @Гречка, Ярик", person)
    assert not contacts.matches_input("Написать @Ярик, Гречка", person)


def test_matches_input_не_путает_имя_с_его_продолжением():
    """«Твикс 12» не выдаёт себя за «Твикс 1»."""
    twix = {"discord": "Твикс 1", "aliases": ["твикс"]}
    assert contacts.matches_input("Написать @Твикс 1", twix)
    assert not contacts.matches_input("Написать @Твикс 12", twix)


def test_matches_input_требует_имя_сразу_после_собаки():
    person = PEOPLE["гоша"]
    assert not contacts.matches_input("Написать в канал про Гречка", person)
    assert not contacts.matches_input("Написать @не_Гречка", person)


def test_matches_input_не_зависит_от_регистра():
    # Раньше префикс сверялся регистрозависимо, а имя — нет.
    person = PEOPLE["гоша"]
    assert contacts.matches_input("написать @гречка", person)
    assert contacts.matches_input("НАПИСАТЬ @ГРЕЧКА", person)


def test_matches_input_терпит_лишние_пробелы():
    person = PEOPLE["гоша"]
    assert contacts.matches_input("  Написать @Гречка  ", person)


def test_человек_без_поля_discord_ничему_не_совпадает():
    """Предикаты обязаны вернуть False, а не уронить KeyError.

    Их зовёт discord_ui.find, который оборачивает try/except только чтение
    свойств элемента, — исключение отсюда долетело бы до общего обработчика.
    """
    broken = {"username": "petya", "aliases": ["петя"]}
    assert contacts.discord_name(broken) == ""
    assert not contacts.matches_dm("Гречка (личное сообщение)", broken)
    assert not contacts.matches_input("Написать @Гречка", broken)
