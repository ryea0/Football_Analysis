from pathlib import Path

_BOM = b"\xef\xbb\xbf"


def read_csv_text(path: Path) -> str:
    """读 CSV 缓存字节：UTF-8 BOM 则 utf-8-sig，否则 latin-1（football-data 惯用编码）。"""
    data = path.read_bytes()
    if data.startswith(_BOM):
        return data.decode("utf-8-sig")
    return data.decode("latin-1")
