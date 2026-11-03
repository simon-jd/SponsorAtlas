"""Run real CDK components against synthetic documented API response shapes."""

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import yaml

MANIFEST = Path("ingestion/board_api/manifest.yaml")


@pytest.fixture(scope="module")
def cdk_source():
    # Only these pinned-CDK import warnings are expected. pytest re-emits anything
    # outside the exact message/category, rather than hiding warnings globally.
    with pytest.warns(
        DeprecationWarning, match=r"^This class is experimental\. Use at your own risk\.$"
    ) as caught:
        from airbyte_cdk.sources.declarative.concurrent_declarative_source import (
            ConcurrentDeclarativeSource,
        )
    from airbyte_cdk.sources.source import ExperimentalClassWarning

    assert len(caught) == 3
    assert all(w.category is ExperimentalClassWarning for w in caught)
    return ConcurrentDeclarativeSource


def read_board(monkeypatch, provider, pages, source_class):
    manifest = yaml.safe_load(MANIFEST.read_text())
    manifest["streams"] = [s for s in manifest["streams"] if s["name"] == provider]
    manifest["check"]["stream_names"] = [provider]
    calls = []

    def send(self, request, **kwargs):
        calls.append(request)
        assert request.method == "GET"
        page = len(calls) - 1
        assert page < len(pages), "Unexpected pagination/infinite-loop request"
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(pages[page]).encode()
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    source = source_class(source_config=manifest, config={"board": "fixture"})
    stream = source.streams({"board": "fixture"})[0]
    records = [
        dict(record) for partition in stream.generate_partitions() for record in partition.read()
    ]
    return records, calls


@pytest.mark.parametrize(
    "provider,payload",
    [
        (
            "greenhouse",
            {
                "jobs": [
                    {
                        "id": 10,
                        "internal_job_id": None,
                        "title": "Engineer",
                        "unexpectedHeader": {"deep": 1},
                    }
                ],
                "meta": {"total": 1},
            },
        ),
        (
            "ashby",
            {
                "apiVersion": "1",
                "jobs": [
                    {
                        "jobUrl": "https://jobs.ashbyhq.com/x/1",
                        "title": "Engineer",
                        "isListed": False,
                    }
                ],
            },
        ),
    ],
)
def test_public_single_page_contract(monkeypatch, provider, payload, cdk_source):
    original = copy.deepcopy(payload["jobs"][0])
    records, calls = read_board(monkeypatch, provider, [payload], cdk_source)
    assert len(records) == 1
    assert len(calls) == 1
    assert records[0]["source_provider"] == provider
    assert records[0]["source_board"] == "fixture"
    assert records[0]["job_key"].startswith(provider + ":fixture:")
    assert json.loads(records[0]["payload_json"]) == original
    params = parse_qs(urlsplit(calls[0].url).query)
    assert params == (
        {"content": ["true"]} if provider == "greenhouse" else {"includeCompensation": ["true"]}
    )


def test_lever_paginates_until_short_page(monkeypatch, cdk_source):
    first = [{"id": f"id-{i}", "text": "Engineer"} for i in range(100)]
    final = [{"id": "last", "text": "Engineer", "future_field": 7}]
    records, calls = read_board(monkeypatch, "lever", [first, final], cdk_source)
    assert len(records) == 101
    assert [parse_qs(urlsplit(call.url).query)["skip"] for call in calls] == [["0"], ["100"]]
    assert all(parse_qs(urlsplit(call.url).query)["limit"] == ["100"] for call in calls)
    assert json.loads(records[-1]["payload_json"])["future_field"] == 7
    assert records[-1]["job_key"] == "lever:fixture:last"


def test_empty_board_is_valid(monkeypatch, cdk_source):
    records, calls = read_board(monkeypatch, "lever", [[]], cdk_source)
    assert records == [] and len(calls) == 1


@pytest.mark.parametrize(
    "provider,payload",
    [("greenhouse", {"error": "renamed_jobs"}), ("ashby", {"jobs": {}}), ("lever", {"jobs": []})],
)
def test_invalid_response_refuses_empty_snapshot(monkeypatch, provider, payload, cdk_source):
    with pytest.raises(Exception, match="Unexpected public board API response shape"):
        read_board(monkeypatch, provider, [payload], cdk_source)
