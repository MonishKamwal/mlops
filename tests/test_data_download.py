"""Tests for the QuickDraw downloader — no network involved."""

import io
import urllib.request
from pathlib import Path

import pytest

from quickdraw.data import download


def test_class_url_quotes_spaces() -> None:
    assert download.class_url("ice cream").endswith("/ice%20cream.npy")


def test_download_writes_file_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []

    def fake_urlopen(url: str) -> io.BytesIO:
        requested.append(url)
        return io.BytesIO(b"fake-npy-bytes")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    path = download.download_class("cat", tmp_path)
    assert path == tmp_path / "cat.npy"
    assert path.read_bytes() == b"fake-npy-bytes"
    assert not list(tmp_path.glob("*.part")), "temp file left behind"
    assert requested == [download.class_url("cat")]


def test_download_skips_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "cat.npy"
    target.write_bytes(b"already here")

    def fail_urlopen(url: str) -> None:
        raise AssertionError("network touched for an existing file")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    assert download.download_class("cat", tmp_path) == target
    assert target.read_bytes() == b"already here"
