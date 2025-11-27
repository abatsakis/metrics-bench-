import os
import time
import threading
import random
from datetime import datetime, timezone
import json

import requests
from prometheus_client import start_http_server, Gauge

# --- config from env ---
NUM_INSTANCES = int(os.getenv("NUM_INSTANCES", "1000"))
NUM_STATUS_CODES = int(os.getenv("NUM_STATUS_CODES", "5"))
NUM_METHODS = int(os.getenv("NUM_METHODS", "2"))
TICK_SECONDS = float(os.getenv("TICK_SECONDS", "1"))

ES_URL = os.getenv("ES_URL", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "metrics-http")

STATUS_CODES = ["200", "500", "400", "404", "502"][:NUM_STATUS_CODES]
METHODS = ["GET", "POST"][:NUM_METHODS]

# Prometheus metric
http_qps = Gauge(
    "http_requests_qps",
    "Synthetic HTTP request QPS",
    ["job", "instance", "status_code", "method"],
)


def wait_for_elasticsearch(max_wait=60, check_interval=2):
    """Wait for Elasticsearch to be ready and accepting connections."""
    print(f"[metrics-gen] Waiting for Elasticsearch at {ES_URL}...")
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        try:
            resp = requests.get(f"{ES_URL}/_cluster/health", timeout=5)
            if resp.status_code == 200:
                health = resp.json()
                if health.get("status") in ("green", "yellow"):
                    print(f"[metrics-gen] Elasticsearch is ready (status: {health.get('status')})")
                    return True
                else:
                    print(f"[metrics-gen] Elasticsearch is up but not ready (status: {health.get('status')}), waiting...")
        except Exception as e:
            pass  # Connection refused or other error, keep waiting
        
        time.sleep(check_interval)
    
    print(f"[metrics-gen] ERROR: Elasticsearch did not become ready within {max_wait} seconds")
    return False


def ensure_index():
    """Create TSDS (Time Series Data Stream) index template (idempotent)."""
    template_name = f"{ES_INDEX}-template"
    # For TSDS, the pattern should match both the data stream name and backing indices
    # Pattern "metrics-http*" matches "metrics-http" (stream) and "metrics-http-000001" (backing indices)
    index_pattern = f"{ES_INDEX}*"
    
    try:
        # First, delete any existing regular index with the same name (not a data stream)
        # This prevents conflicts if a regular index was created before
        try:
            check_resp = requests.head(f"{ES_URL}/{ES_INDEX}", timeout=5)
            if check_resp.status_code == 200:
                # Check if it's a regular index (not a data stream)
                get_resp = requests.get(f"{ES_URL}/{ES_INDEX}", timeout=5)
                if get_resp.status_code == 200:
                    index_info = get_resp.json()
                    # If it's not a data stream, delete it
                    if ES_INDEX not in [ds.get("name") for ds in requests.get(f"{ES_URL}/_data_stream", timeout=5).json().get("data_streams", [])]:
                        print(f"[metrics-gen] Deleting existing regular index '{ES_INDEX}' to use TSDS")
                        requests.delete(f"{ES_URL}/{ES_INDEX}", timeout=5)
        except Exception:
            pass  # Index doesn't exist or other error, that's fine
        
        template = {
            "index_patterns": [index_pattern],
            "data_stream": {},
            "template": {
                "settings": {
                    "index.mode": "time_series",
                    "index.translog.durability": "async",
                    "index.translog.sync_interval": "10s",
                    "index.refresh_interval": "5s",
                },
                "mappings": {
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "http_requests_qps": {
                            "type": "double",
                            "time_series_metric": "gauge"
                        },
                        "job": {"type": "keyword", "time_series_dimension": True},
                        "instance": {"type": "keyword", "time_series_dimension": True},
                        "status_code": {"type": "keyword", "time_series_dimension": True},
                        "method": {"type": "keyword", "time_series_dimension": True},
                    }
                }
            }
        }
        
        # Create or update the index template
        r = requests.put(f"{ES_URL}/_index_template/{template_name}", json=template, timeout=10)
        if r.status_code not in (200, 201):
            print(f"[metrics-gen] template create status {r.status_code}: {r.text[:200]}")
            return False
        
        # Verify template was created
        verify_resp = requests.get(f"{ES_URL}/_index_template/{template_name}", timeout=5)
        if verify_resp.status_code != 200:
            print(f"[metrics-gen] WARNING: Could not verify template creation")
            return False
        
        # Wait a moment for template to be fully registered
        time.sleep(0.5)
        
        # Explicitly create the data stream so it exists before queries run
        stream_name = ES_INDEX
        r = requests.put(f"{ES_URL}/_data_stream/{stream_name}", timeout=10)
        if r.status_code not in (200, 201):
            # If stream already exists, that's fine
            if r.status_code == 400 and "resource_already_exists_exception" in r.text.lower():
                print(f"[metrics-gen] Data stream '{stream_name}' already exists")
            else:
                print(f"[metrics-gen] Data stream create status {r.status_code}: {r.text[:200]}")
                print(f"[metrics-gen] Will continue - stream will auto-create on first document")
        else:
            print(f"[metrics-gen] Data stream '{stream_name}' created successfully")
        
        print("[metrics-gen] TSDS template and data stream ready")
        return True
    except Exception as e:
        print(f"[metrics-gen] failed to create TSDS template/stream: {e}")
        return False


def generate_series():
    """Yield all label combinations for synthetic series."""
    for i in range(NUM_INSTANCES):
        instance = f"inst-{i:05d}"
        for sc in STATUS_CODES:
            for m in METHODS:
                yield {
                    "job": "demo",
                    "instance": instance,
                    "status_code": sc,
                    "method": m,
                }


def build_bulk_payload(timestamp_iso):
    """Build NDJSON bulk body for one tick."""
    lines = []
    for labels in generate_series():
        # synthetic value
        base = 100.0
        noise = random.uniform(-10, 10)
        factor = 1.0 if labels["status_code"] == "200" else 0.05
        value = max(0.0, base * factor + noise)

        # update Prometheus gauge
        http_qps.labels(
            job=labels["job"],
            instance=labels["instance"],
            status_code=labels["status_code"],
            method=labels["method"],
        ).set(value)

        action = {"create": {}}
        doc = {
            "@timestamp": timestamp_iso,
            "http_requests_qps": value,
            "job": labels["job"],
            "instance": labels["instance"],
            "status_code": labels["status_code"],
            "method": labels["method"],
        }
        lines.append(json.dumps(action))
        lines.append(json.dumps(doc))

    # final newline at end is recommended
    return "\n".join(lines) + "\n"


def ingest_loop():
    # Wait for Elasticsearch to be ready first
    if not wait_for_elasticsearch():
        print("[metrics-gen] ERROR: Elasticsearch not available, cannot proceed")
        return
    
    # Ensure template is created and verified BEFORE indexing any data
    max_retries = 3
    for attempt in range(max_retries):
        if ensure_index():
            break
        if attempt < max_retries - 1:
            print(f"[metrics-gen] Template setup failed, retrying in 2 seconds... (attempt {attempt + 1}/{max_retries})")
            time.sleep(2)
        else:
            print("[metrics-gen] ERROR: Failed to create template after retries. Data may not use TSDS!")
    
    print(
        f"[metrics-gen] ingest loop starting: "
        f"instances={NUM_INSTANCES}, status={STATUS_CODES}, methods={METHODS}, "
        f"tick={TICK_SECONDS}s"
    )

    # Index initial batch immediately to ensure data stream has backing indices
    first_batch = True
    while True:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            body = build_bulk_payload(ts)
            resp = requests.post(
                f"{ES_URL}/{ES_INDEX}/_bulk",
                data=body,
                headers={"Content-Type": "application/x-ndjson"},
                timeout=60,
            )
            if resp.status_code >= 300:
                print(f"[metrics-gen] bulk error {resp.status_code}: {resp.text[:200]}")
            elif first_batch:
                # Verify data stream exists and has backing indices after first batch
                first_batch = False
                try:
                    # Refresh to make sure the data is searchable
                    refresh_resp = requests.post(f"{ES_URL}/{ES_INDEX}/_refresh", timeout=5)
                    if refresh_resp.status_code in (200, 201):
                        # Check data stream status
                        stream_resp = requests.get(f"{ES_URL}/_data_stream/{ES_INDEX}", timeout=5)
                        if stream_resp.status_code == 200:
                            stream_data = stream_resp.json()
                            indices = stream_data.get("data_streams", [{}])[0].get("indices", [])
                            if indices:
                                print(f"[metrics-gen] Data stream '{ES_INDEX}' ready with {len(indices)} backing index(es)")
                            else:
                                print(f"[metrics-gen] WARNING: Data stream exists but no backing indices yet")
                        else:
                            print(f"[metrics-gen] WARNING: Could not verify data stream status")
                except Exception as e:
                    print(f"[metrics-gen] WARNING: Could not verify data stream: {e}")
        except Exception as e:
            print(f"[metrics-gen] error sending bulk: {e}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    # Start Prometheus exporter
    start_http_server(8000)
    # Start ES ingest in background thread
    t = threading.Thread(target=ingest_loop, daemon=True)
    t.start()
    # Keep main thread alive
    while True:
        time.sleep(3600)
