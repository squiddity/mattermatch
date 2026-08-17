import io
import pytest

from mattermatch.inventory import InventoryError, load_inventory, normalize_code


def test_normalize_visual_separators_and_preserve_descriptor():
    assert normalize_code(" 00-2048 0000-2 ") == "00204800002"
    inventory = load_inventory(io.StringIO("code,descriptor\n00-2048 0000-2,  keep spaces  \n"))
    assert inventory.entries["00204800002"] == "  keep spaces  "


def test_bom_quoted_csv_and_empty_lines(tmp_path):
    path = tmp_path / "inventory.csv"
    path.write_bytes(b"\xef\xbb\xbfc\x6f\x64\x65,descriptor\n\n00204800002,\"a,b\"\n")
    assert load_inventory(path).entries["00204800002"] == "a,b"


@pytest.mark.parametrize(
    "text",
    ["", "123", "00204800001", "0020480000x2", "00204800002-0"],
)
def test_invalid_codes_are_fatal(text):
    with pytest.raises(InventoryError):
        normalize_code(text)


def test_duplicate_and_bad_schema_are_fatal():
    with pytest.raises(InventoryError):
        load_inventory(io.StringIO("code,descriptor\n00204800002,a\n002-048 00002,b\n"))
    with pytest.raises(InventoryError):
        load_inventory(io.StringIO("descriptor,code\n00204800002,a\n"))
    with pytest.raises(InventoryError):
        load_inventory(io.StringIO("code,descriptor\n00204800002,a,b\n"))
