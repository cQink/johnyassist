from johnny import morph


def test_stem_отрезает_хвостовую_гласную():
    assert morph.stem("серёги") == "серег"
    assert morph.stem("гоше") == "гош"


def test_stem_не_трогает_короткие_слова():
    assert morph.stem("оба") == "оба"


def test_stem_не_трогает_согласную_на_конце():
    assert morph.stem("георгий") == "георгий"


def test_stem_phrase_по_словам():
    assert morph.stem_phrase("Дядя Вася") == "дяд вас"
