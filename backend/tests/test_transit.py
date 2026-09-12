"""在途期間對照表查表:遮罩地址不必整筆棄權。"""
import pytest

from app.transit import lookup_transit_days, resolve_transit_days, residence_key

_AGENCY = "新北市"  # settings.APPEAL_AGENCY_LOCATION 的預設值,即本系統的受理訴願機關所在地


def test_masked_district_still_resolves_when_both_groups_agree():
    """住居所被遮罩成「臺中市○○區」時,附表(一)(二)對新北市都是 5 日,之分一天都不差。"""
    result = resolve_transit_days("臺中市○○區中山路1號", _AGENCY)

    assert result.days == 5
    assert result.review_note == ""


@pytest.mark.parametrize(
    "residence,expected",
    [
        ("臺中市○○區中山路1號", 5),
        ("臺南市○○區民生路2號", 4),
        ("高雄市○○區三多路3號", 6),
    ],
)
def test_masked_districts_of_all_split_cities_resolve_for_this_agency(residence, expected):
    """附表把臺中/臺南/高雄依行政區拆成(一)(二);對新北市三市的兩組日數全部相同。"""
    assert resolve_transit_days(residence, _AGENCY).days == expected


def test_unmasked_district_is_unaffected():
    """行政區寫得出來時仍走原本的逐區查表,不因新增遮罩分支而改變。"""
    assert resolve_transit_days("臺中市西屯區台灣大道3號", _AGENCY).days == 5


def test_masked_district_declines_when_groups_differ_and_says_which():
    """兩組日數不同時仍不算——但要講清楚是哪個城市的哪兩組差幾日,不是籠統的「無法認定」。
    以臺北市為受理機關:高雄市(一)5 日、(二)6 日。"""
    result = resolve_transit_days("高雄市○○區三多路3號", "臺北市")

    assert result.days is None
    assert "高雄市" in result.review_note
    assert "（一）" in result.review_note and "（二）" in result.review_note
    assert "１" in result.review_note  # 差 1 日


def test_masked_district_does_not_take_the_longer_of_two_different_values():
    """不得以「取較長日數對人民有利」補不同的那些格:在途期間辦法附表沒有這條規則。"""
    result = resolve_transit_days("高雄市○○區三多路3號", "臺北市")

    assert result.days != 6
    assert result.days is None


def test_unknown_city_still_declines():
    """完全查不到的住居所仍回 None,遮罩分支不得把「查不到」變成「猜一個」。"""
    result = resolve_transit_days("日本國東京都千代田區1番地", _AGENCY)

    assert result.days is None
    assert result.review_note == ""  # 非「兩組不同」的情形,不編造差異說明


def test_masked_district_keeps_residence_key_none():
    """residence_key 的語意不變:行政區不明就是判不出欄名。遮罩的補救在 resolve 層,
    不是把 residence_key 改成會猜一個欄名回來。"""
    assert residence_key("臺中市○○區中山路1號") is None


def test_lookup_transit_days_stays_compatible():
    """既有呼叫端用的簡單版仍在,且遮罩地址現在也查得出來(與 resolve 同一份判斷)。"""
    assert lookup_transit_days("臺中市西屯區台灣大道3號", _AGENCY) == 5
    assert lookup_transit_days("臺中市○○區中山路1號", _AGENCY) == 5
    assert lookup_transit_days("高雄市○○區三多路3號", "臺北市") is None
