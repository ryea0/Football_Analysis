from fa.data.reader import read_csv_text


def test_latin1_and_bom(tmp_path):
    p = tmp_path / "a.csv"
    p.write_bytes("Div,Date\nE0,x\n".encode("latin-1"))
    assert read_csv_text(p) == "Div,Date\nE0,x\n"

    b = tmp_path / "b.csv"
    b.write_bytes(b"\xef\xbb\xbfDiv,Date\n")
    assert read_csv_text(b) == "Div,Date\n"        # BOM 被剥掉


def test_latin1_high_bytes(tmp_path):
    p = tmp_path / "c.csv"
    p.write_bytes("Bayern M\xfcnchen\n".encode("latin-1"))
    assert read_csv_text(p) == "Bayern München\n"
