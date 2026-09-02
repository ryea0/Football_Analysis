import urllib.error

from fa.config import csv_url, season_code
from fa.data.download import csv_cache_path, download_csv


def test_url_construction():
    assert csv_url("E0", 2025) == "https://www.football-data.co.uk/mmz4281/2526/E0.csv"
    assert season_code(1999) == "9900"
    assert season_code(2025) == "2526"


def test_cache_path_format():
    p = csv_cache_path("D1", 2024)
    assert p.name == "D1_2425.csv"


def test_cached_file_no_network(tmp_path, monkeypatch):
    # 先打补丁再算缓存路径：csv_cache_path 依赖被 mock 的 csv_cache_dir，
    # 否则会写到真实缓存目录并触发网络。
    monkeypatch.setattr("fa.data.download.csv_cache_dir", lambda: tmp_path)
    p = csv_cache_path("E0", 2025)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("Div,Date\n")

    def boom(*a, **k):  # 若触网立即失败
        raise AssertionError("network touched")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert download_csv("E0", 2025) == p


def test_404_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr("fa.data.download.csv_cache_dir", lambda: tmp_path)

    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert download_csv("F1", 1993) is None


def test_download_writes_cache_atomically(tmp_path, monkeypatch):
    monkeypatch.setattr("fa.data.download.csv_cache_dir", lambda: tmp_path)
    p = tmp_path / "E0_9900.csv"
    p.write_text("stale")  # refresh=True 应无视已有缓存重新下载

    content = b"Div,Date,HomeTeam\nE0,2025-08-16,Chelsea\n"

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return content

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: FakeResp())

    assert download_csv("E0", 1999, refresh=True) == p
    assert p.read_bytes() == content
    # 原子写入：不得残留 .part 临时文件
    assert not p.with_suffix(".csv.part").exists()
    assert [f.name for f in tmp_path.iterdir()] == ["E0_9900.csv"]
