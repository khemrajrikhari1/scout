from gcc_procure.normalize import *


def test_email_validation():
    assert normalize_email(" Procurement@Lulu.AE ") == "procurement@lulu.ae"
    assert normalize_email("mailto:buyer@x.ae?subject=hi") == "buyer@x.ae"
    for bad in ["", "foo", "a@b", "a@@b.com", "logo@2x.png", "me@example.com", "a..b@x.com", None]:
        assert normalize_email(bad) == "", bad


def test_phone_normalisation_gcc():
    assert normalize_phone("04 345 6789", "AE") == ("+97143456789", True)
    assert normalize_phone("+971 4 234 5678", "AE")[1]
    assert normalize_phone("800 5858", "AE") == ("+9718005858", True)  # UAE toll-free
    assert normalize_phone("04 123 4567", "AE") == ("", False)  # not a real UAE number range
    assert normalize_phone("+966 11 462 1111", "SA") == ("+966114621111", True)
    assert normalize_phone("12345", "AE") == ("", False)
    assert normalize_phone("", "AE") == ("", False)


def test_company_name_key_dedupes_variants():
    assert name_key("Lulu Hypermarket L.L.C") == name_key("LULU HYPERMARKET LLC.") == name_key("The Lulu Hypermarket")
    assert name_key("Al Maya Group") == name_key("Al Maya")
    assert name_key("Spinneys") != name_key("Choithrams")


def test_url_normalisation():
    assert normalize_url("WWW.Example.AE/") == "https://www.example.ae"
    assert domain_of("https://www.luluhypermarket.com/en-ae/") == "luluhypermarket.com"
    assert normalize_url("javascript:alert(1)") == ""
    assert same_site("https://shop.lulu.ae/x", "https://lulu.ae")


def test_designation_tiers():
    assert classify_designation("Head of Procurement") == 1
    assert classify_designation("Senior Buyer - FMCG") == 1
    assert classify_designation("Category Manager, Food") == 1
    assert classify_designation("Purchase Manager") == 1
    assert classify_designation("Supply Chain Manager") == 2
    assert classify_designation("Pharmacy Category Buyer") == 1
    assert classify_designation("Commercial Manager") == 2
    assert classify_designation("Managing Director") == 3
    assert classify_designation("General Manager") == 3
    assert classify_designation("HR Manager") == 0
    assert classify_designation("Head Pharmacist") == 0
    assert classify_designation("Marketing Manager") == 0
    assert classify_designation("") == 0
