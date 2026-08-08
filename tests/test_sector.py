"""Runnable checks for the SIC -> sector ETF mapping - the one genuinely
non-trivial, network-free piece of the relative-strength feature. Two ordering
bugs were caught here during implementation: a 3-digit carve-out (384, medical
instruments) sitting after its broader 2-digit bucket (38) in the list never
matched, and the same shape of bug for food stores (54) vs retail (52-59).
These tests pin the fix so it can't silently regress."""
from insider.sector import sic_to_etf


def test_semiconductors_maps_to_tech():
    assert sic_to_etf(3674) == "XLK"  # Micron's real SIC code, verified live during planning


def test_electric_utility_maps_to_utilities():
    assert sic_to_etf(4911) == "XLU"


def test_missing_sic_falls_back_to_spy():
    assert sic_to_etf(None) == "SPY"


def test_unmapped_sic_falls_back_to_spy():
    assert sic_to_etf(99999) == "SPY"


def test_medical_instrument_carveout_beats_broader_instruments_bucket():
    # sic // 100 == 38 (general instruments -> XLK), but sic // 10 == 384
    # (medical instruments) must win - this is the ordering bug that was fixed.
    assert sic_to_etf(3841) == "XLV"
    assert sic_to_etf(3826) == "XLK"  # a real 38-bucket code that ISN'T medical


def test_food_store_carveout_beats_broader_retail_bucket():
    assert sic_to_etf(5411) == "XLP"  # grocery stores
    assert sic_to_etf(5311) == "XLY"  # department stores - general retail bucket


def test_fitness_facilities_are_not_communication_services():
    # caught live: Life Time Group (gyms, SIC 7991) was falling into an
    # earlier, too-broad "70-79 -> XLC" rule alongside motion pictures.
    assert sic_to_etf(7991) == "XLY"   # physical fitness facilities
    assert sic_to_etf(7812) == "XLC"   # motion picture production - the real XLC case in that range


if __name__ == "__main__":
    test_semiconductors_maps_to_tech()
    test_electric_utility_maps_to_utilities()
    test_missing_sic_falls_back_to_spy()
    test_unmapped_sic_falls_back_to_spy()
    test_medical_instrument_carveout_beats_broader_instruments_bucket()
    test_food_store_carveout_beats_broader_retail_bucket()
    test_fitness_facilities_are_not_communication_services()
    print("ok")
