from parse_decisions import clean_case_type


def test_the_wrapper_words_around_a_case_type_are_stripped():
    assert clean_case_type("違反噪音管制法事件") == "噪音管制法"
    assert clean_case_type("都市更新事件") == "都市更新"


def test_the_air_pollution_act_spelling_variants_collapse_to_one_case_type():
    # 來源檔名同一部法出現三種寫法,F3 以 case_type 完全相等硬過濾,不對齊就查不到彼此
    assert clean_case_type("違反空氣汙染防制法事件") == "空氣污染防制法"
    assert clean_case_type("違反空氣汙染管制法事件") == "空氣污染防制法"
    assert clean_case_type("違反空氣污染防制法事件") == "空氣污染防制法"


def test_case_types_with_no_alias_are_left_exactly_as_written():
    assert clean_case_type("違反廢棄物清理法事件") == "廢棄物清理法"
    assert clean_case_type("社會救助事件") == "社會救助"
