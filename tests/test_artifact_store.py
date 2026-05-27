"""Tests for artifact storage backends."""

from __future__ import annotations

import json

import boto3

from research_core.storage import LocalArtifactStore, S3ArtifactStore, artifact_store, ticker_artifact_key


def test_local_artifact_store_writes_text_json_and_bytes(tmp_path):
    store = LocalArtifactStore(tmp_path)

    text_uri = store.write_text("AAPL/report.txt", "hello")
    json_uri = store.write_json("AAPL/report.json", {"ok": True})
    bytes_uri = store.write_bytes("AAPL/raw.bin", b"abc")

    assert (tmp_path / "AAPL" / "report.txt").read_text() == "hello"
    assert json.loads((tmp_path / "AAPL" / "report.json").read_text()) == {"ok": True}
    assert (tmp_path / "AAPL" / "raw.bin").read_bytes() == b"abc"
    assert text_uri.endswith("report.txt")
    assert json_uri.endswith("report.json")
    assert bytes_uri.endswith("raw.bin")


def test_local_artifact_store_deletes_owned_uri(tmp_path):
    store = LocalArtifactStore(tmp_path)
    uri = store.write_text("AAPL/tmp/report.txt", "hello")

    assert store.delete_uri(uri)
    assert not (tmp_path / "AAPL" / "tmp" / "report.txt").exists()
    assert not store.delete_uri(str(tmp_path.parent / "outside.txt"))


def test_artifact_store_factory_defaults_to_local(monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "local")
    monkeypatch.setenv("SCOPE_ARTIFACTS_DIR", str(tmp_path))

    store = artifact_store()
    store.write_text("MSFT/a.txt", "x")

    assert (tmp_path / "MSFT" / "a.txt").read_text() == "x"


def test_minio_artifact_store_writes_s3_compatible_objects(monkeypatch):
    calls = []

    class FakeClient:
        def put_object(self, **kwargs):
            calls.append(kwargs)

        def delete_object(self, **kwargs):
            calls.append({"delete": kwargs})

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeClient())

    store = S3ArtifactStore(
        bucket="scope-artifacts",
        prefix="scope",
        endpoint_url="http://localhost:9000",
        region_name="us-east-1",
        backend_name="minio",
    )
    uri = store.write_json("LLY/synthesis/final_synthesis.json", {"ok": True})

    assert store.backend_name == "minio"
    assert uri == "s3://scope-artifacts/scope/LLY/synthesis/final_synthesis.json"
    assert calls[0]["Bucket"] == "scope-artifacts"
    assert calls[0]["Key"] == "scope/LLY/synthesis/final_synthesis.json"
    assert calls[0]["ContentType"] == "application/json"
    assert json.loads(calls[0]["Body"].decode("utf-8")) == {"ok": True}
    assert store.delete_uri(uri)
    assert calls[1]["delete"]["Bucket"] == "scope-artifacts"
    assert calls[1]["delete"]["Key"] == "scope/LLY/synthesis/final_synthesis.json"


def test_artifact_store_factory_supports_minio(monkeypatch):
    calls = []

    class FakeClient:
        def put_object(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeClient())
    monkeypatch.setenv("ARTIFACT_STORE_BACKEND", "minio")
    monkeypatch.setenv("ARTIFACT_BUCKET", "scope-artifacts")
    monkeypatch.setenv("ARTIFACT_PREFIX", "scope")
    monkeypatch.setenv("ARTIFACT_S3_ENDPOINT_URL", "http://localhost:9000")

    store = artifact_store()
    uri = store.write_text("AAPL/report.txt", "hello")

    assert getattr(store, "backend_name") == "minio"
    assert uri == "s3://scope-artifacts/scope/AAPL/report.txt"
    assert calls[0]["Body"] == b"hello"


def test_ticker_artifact_key_normalizes_ticker():
    assert ticker_artifact_key(" lly ", "documents", "raw", "a.pdf") == "LLY/documents/raw/a.pdf"
